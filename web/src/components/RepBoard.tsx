import { useMemo } from "react";

import { clock, duration, hourLabel, int, minutes, num, pace, paceTone, pct } from "../format";
import type { RepCard, RepStatus } from "../types";

const STATUS_LABEL: Record<RepStatus, string> = {
  aktif: "Aktif",
  bosta: "Boşta",
  hareketsiz: "Hareketsiz",
  baslamadi: "Başlamadı",
  mesai_disi: "Mesai dışı",
  gecmis: "Geçmiş gün",
};

const STATUS_ORDER: Record<RepStatus, number> = {
  hareketsiz: 0,
  bosta: 1,
  baslamadi: 2,
  aktif: 3,
  mesai_disi: 4,
  gecmis: 5,
};

export function StatusPill({ status }: { status: RepStatus }) {
  return (
    <span className={`status ${status}`}>
      <span className={`dot ${status}`} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function PaceMeter({ actual, expected }: { actual: number; expected: number }) {
  const ratio = pace(actual, expected);
  const tone = paceTone(ratio);
  const scaleMax = Math.max(actual, expected, 1) * 1.15;
  return (
    <div className="meter" title={`Gerçekleşen ${int(actual)} · beklenen ${num(expected)}`}>
      <div
        className={`fill ${tone === "neutral" ? "" : tone}`}
        style={{ width: `${(actual / scaleMax) * 100}%` }}
      />
      {expected > 0 ? (
        <div className="mark" style={{ left: `calc(${(expected / scaleMax) * 100}% - 1px)` }} />
      ) : null}
    </div>
  );
}

function heat(v: number, max: number): string {
  if (v <= 0) return "";
  const q = v / Math.max(max, 1);
  return q > 0.75 ? "h4" : q > 0.5 ? "h3" : q > 0.25 ? "h2" : "h1";
}

function HourStrip({ card, nowHour }: { card: RepCard; nowHour: number | null }) {
  const max = Math.max(...card.hours.map((h) => Math.max(h.today.arama, h.expected.arama)), 1);
  return (
    <div className="strip" aria-label="Saatlik arama">
      {card.hours.map((h) => {
        const future = nowHour !== null && h.hour > nowHour;
        return (
          <span
            key={h.hour}
            className={future ? "future" : heat(h.today.arama, max)}
            title={`${hourLabel(h.hour)} · ${int(h.today.arama)} arama (ort. ${num(h.expected.arama)})`}
          />
        );
      })}
    </div>
  );
}

interface Props {
  reps: RepCard[];
  nowHour: number | null;
  warnIdle: number;
}

export function RepBoard({ reps, nowHour, warnIdle }: Props) {
  const rows = useMemo(
    () =>
      [...reps].sort(
        (a, b) =>
          STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
          (b.idle_min ?? 0) - (a.idle_min ?? 0) ||
          a.name.localeCompare(b.name, "tr"),
      ),
    [reps],
  );

  if (!rows.length) return <div className="empty">Temsilci yok.</div>;

  return (
    <div className="table-wrap">
      <table className="board">
        <thead>
          <tr>
            <th>Temsilci</th>
            <th>Durum</th>
            <th className="num">Hareketsiz</th>
            <th>Son faaliyet</th>
            <th>Son CRM işlemi</th>
            <th>Arama · tempo</th>
            <th className="num">Ulaşılan</th>
            <th className="num">Ulaşma</th>
            <th className="num">Randevu</th>
            <th className="num">Ort. görüşme</th>
            <th>Doluluk</th>
            <th>Saatlik arama</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const idleAlert = r.idle_min !== null && r.idle_min >= warnIdle;
            return (
              <tr key={r.rep_id}>
                <td>
                  <div className="name">{r.name}</div>
                  <div className="sub">ilk faaliyet {clock(r.first_at)}</div>
                </td>
                <td>
                  <StatusPill status={r.status} />
                </td>
                <td className={`num ${idleAlert ? "idle-alert" : ""}`}>{minutes(r.idle_min)}</td>
                <td>
                  <div>{clock(r.last_active_at)}</div>
                  <div className="sub">{lastKind(r)}</div>
                </td>
                <td>
                  {r.last_crm_at ? (
                    clock(r.last_crm_at)
                  ) : (
                    <span className="muted" title="CRM işlem akışı bağlanınca dolacak">
                      veri bekleniyor
                    </span>
                  )}
                </td>
                <td className="pace-cell">
                  <div className="row">
                    <b>{int(r.today.arama)}</b>
                    <span className="muted">beklenen {num(r.expected.arama)}</span>
                  </div>
                  <PaceMeter actual={r.today.arama} expected={r.expected.arama} />
                </td>
                <td className="num">{int(r.today.ulasilan)}</td>
                <td className="num">{pct(r.today.ulasma_orani)}</td>
                <td className="num">
                  {int(r.today.randevu)}
                  {r.today.katilim_payda ? (
                    <div className="sub">{int(r.today.katildi)} katıldı</div>
                  ) : null}
                </td>
                <td className="num">{duration(r.today.sure_ort)}</td>
                <td className="pace-cell" style={{ minWidth: 110 }}>
                  <div className="row">
                    <b>{pct(r.today.doluluk)}</b>
                    <span className="muted">{minutes(r.today.dolu_dk)}</span>
                  </div>
                  <div className="meter">
                    <div className="fill" style={{ width: `${Math.min(r.today.doluluk ?? 0, 100)}%` }} />
                  </div>
                </td>
                <td>
                  <HourStrip card={r} nowHour={nowHour} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Hareketsizlik: mesai içinde son arama, toplantı, WhatsApp veya CRM işleminden bu yana
        geçen süre; veri tazeliğiyle sınırlıdır. Tempo çizgisi: son 90 günde aynı gün tipinde bu
        saate kadar yapılan ortalama.
      </p>
    </div>
  );
}

function lastKind(r: RepCard): string {
  const stamps: [string, string | null][] = [
    ["arama", r.last_call_at],
    ["toplantı", r.last_meeting_at],
    ["WhatsApp", r.last_whatsapp_at],
    ["CRM", r.last_crm_at],
  ];
  const best = stamps
    .filter((s): s is [string, string] => s[1] !== null)
    .sort((a, b) => b[1].localeCompare(a[1]))[0];
  return best ? best[0] : "faaliyet yok";
}
