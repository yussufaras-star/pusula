import { useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, money } from "./format";
import type { AlertKind, Alerts, FinAlert } from "./types";
import { CompanyTag, Loading, useFin, type TabProps } from "./ui";

const KIND_LABEL: Record<AlertKind, string> = {
  uretici: "Üretici ödemesi",
  abonelik: "Yeni abonelik",
  olagandisi: "Olağan dışı transfer",
};
const DAYS_LABEL: Record<number, string> = { 1: "1 gün", 3: "3 gün", 7: "1 hafta", 14: "2 hafta", 30: "1 ay" };

type View = "bekleyen" | "riskli" | "kapanan";
const VIEWS: { key: View; label: string }[] = [
  { key: "bekleyen", label: "Karar bekleyen" },
  { key: "riskli", label: "Riskli (hatırlatma bekliyor)" },
  { key: "kapanan", label: "Güvenilen / kapanan" },
];

function inView(a: FinAlert, view: View): boolean {
  if (view === "bekleyen") return a.due;
  if (view === "riskli") return a.status === "riskli" && !a.due;
  return a.status === "guven" || a.status === "kapandi";
}

export function AlertsTab({ scope, tick }: TabProps) {
  const [local, setLocal] = useState(0);
  const [view, setView] = useState<View>("bekleyen");
  const [err, setErr] = useState<string | null>(null);
  const { data, error } = useFin<Alerts>(`/api/finance/alerts?company=${scope}`, tick + local);
  if (!data) return <Loading error={error} />;

  const count = (v: View) => data.alerts.filter((a) => inView(a, v)).length;
  const rows = data.alerts.filter((a) => inView(a, view));
  const due = data.alerts.filter((a) => a.due);

  async function decide(a: FinAlert, action: "guven" | "riskli", remind_days?: number) {
    setErr(null);
    try {
      await api(`/api/finance/alerts/${a.id}`, { method: "PATCH", body: JSON.stringify({ action, remind_days }) });
      setLocal((n) => n + 1);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    }
  }

  return (
    <>
      <div className="tiles fin-tiles">
        <StatTile label="Karar bekleyen" value={due.length} tone={due.length ? "warning" : "neutral"} expected="açık ve hatırlatması gelen" />
        <StatTile label="Üretici ödemesi" value={due.filter((a) => a.kind === "uretici").length} expected="Rexven cüzdanından düşülmemiş" />
        <StatTile label="Yeni abonelik" value={due.filter((a) => a.kind === "abonelik").length} expected="ilk kez çekilen" />
        <StatTile label="Olağan dışı transfer" value={due.filter((a) => a.kind === "olagandisi").length} expected="yeni alıcı, normalin 3 katı, 25.000 $ üstü" />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Uyarılar</h2>
          <div className="chips" role="group" aria-label="Görünüm">
            {VIEWS.map((v) => (
              <button key={v.key} className={`chip${view === v.key ? " on" : ""}`} aria-pressed={view === v.key} onClick={() => setView(v.key)}>
                {v.label} · {count(v.key)}
              </button>
            ))}
          </div>
        </div>
        <div className="card-body">
          <p className="muted fin-note">
            Güven: uyarı kapanır. Riskli: seçtiğin süre sonunda yeniden açılır ve Cliq'ten hatırlatılır. Üretici uyarısı, ödeme sonradan
            Rexven'de üretici cüzdanından düşülürse kendiliğinden kapanır.
          </p>
          {err ? <div className="banner error">{err}</div> : null}
          {rows.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Tarih</th>
                    <th>Tür</th>
                    <th>Hesap</th>
                    <th>Karşı taraf</th>
                    <th className="num">Tutar</th>
                    <th>Neden</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr key={a.id}>
                      <td>{day(a.booked_at)}</td>
                      <td>
                        <span className={`badge ${a.kind === "uretici" ? "critical" : "warning"}`}>{KIND_LABEL[a.kind]}</span>
                        {a.status === "riskli" && a.due ? <span className="badge neutral">hatırlatma</span> : null}
                      </td>
                      <td>
                        <CompanyTag id={a.company_id} /> {a.account}
                      </td>
                      <td title={a.description}>{a.counterparty || a.description.slice(0, 60)}</td>
                      <td className="num">{money(Math.abs(a.amount), a.currency, 2)}</td>
                      <td>{a.reason}</td>
                      <td>
                        <AlertActions alert={a} days={data.remind_days} onDecide={decide} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Bu görünümde uyarı yok.</div>
          )}
        </div>
      </section>
    </>
  );
}

function AlertActions({
  alert,
  days,
  onDecide,
}: {
  alert: FinAlert;
  days: number[];
  onDecide: (a: FinAlert, action: "guven" | "riskli", remind_days?: number) => void;
}) {
  const [asking, setAsking] = useState(false);
  if (alert.status === "kapandi") return <span className="muted">Rexven'de bulundu</span>;
  if (alert.status === "guven") return <span className="muted">Güvenildi {alert.decided_at ? day(alert.decided_at.slice(0, 10)) : ""}</span>;
  if (asking)
    return (
      <div className="controls">
        <select aria-label="Hatırlatma aralığı" defaultValue="" onChange={(e) => e.target.value && onDecide(alert, "riskli", Number(e.target.value))}>
          <option value="" disabled>
            Ne zaman hatırlatayım?
          </option>
          {days.map((d) => (
            <option key={d} value={d}>
              {DAYS_LABEL[d] ?? `${d} gün`} sonra
            </option>
          ))}
        </select>
        <button className="btn" onClick={() => setAsking(false)}>
          Vazgeç
        </button>
      </div>
    );
  return (
    <div className="controls">
      {alert.status === "riskli" && !alert.due && alert.remind_at ? (
        <span className="muted">Hatırlatma {day(alert.remind_at.slice(0, 10))}</span>
      ) : null}
      <button className="btn primary" onClick={() => onDecide(alert, "guven")}>
        Güven
      </button>
      <button className="btn" onClick={() => setAsking(true)}>
        Riskli
      </button>
    </div>
  );
}
