import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { StatTile } from "./StatTile";
import type { ChurnPayload, ChurnPeriod } from "../types";

const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric", timeZone: "UTC" });
const OWNERS = ["Nihat Kemer", "Burcu Özkan"];

const rate = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `%${num(v)}`);

function periodNote(p: ChurnPeriod | undefined): string {
  if (!p) return "";
  return `${int(p.kaybedildi)} kaybedildi · ${int(p.yeniledi)} yeniledi`;
}

/** Aylık churn tablosu: vadesi dolan, yenileyen, kaybedilen, geri kazanılan, iade. */
export function ChurnBoard() {
  const [owner, setOwner] = useState("");
  const { data, error } = usePolling<ChurnPayload>(`/api/churn${owner ? `?owner=${encodeURIComponent(owner)}` : ""}`, 0);
  if (error) return <div className="banner error">Churn verisi alınamadı: {error.message}</div>;
  if (!data) return <div className="skeleton" />;
  if (!data.available) return <div className="empty">Müşteri profilleri ilk saatlik güncellemeyle gelecek.</div>;

  const months = data.aylar ?? [];
  const current = months.find((m) => m.ay === `${new Date().toISOString().slice(0, 7)}-01`);
  const past = months.filter((m) => m.satis > 0 || m.vadesi_gelen > 0);
  const refunds = past.reduce((a, m) => [a[0] + m.iade, a[1] + m.satis], [0, 0]);

  return (
    <>
      <div className="ss-controls">
        <div className="chips" role="group" aria-label="Müşteri sahibi">
          {["", ...OWNERS].map((o) => (
            <button key={o || "all"} className={`chip${owner === o ? " on" : ""}`} onClick={() => setOwner(o)} aria-pressed={owner === o}>
              {o || "Tüm müşteriler"}
            </button>
          ))}
        </div>
      </div>

      <div className="tiles">
        <StatTile label="Bu ay churn" value={rate(current?.churn)} expected={current ? `${int(current.kaybedildi)} / ${int(current.kaybedildi + current.yeniledi)} vadesi geçen` : ""} />
        <StatTile label="Son 12 ay churn" value={rate(data.son_12_ay?.churn)} expected={periodNote(data.son_12_ay)} />
        <StatTile label="Geri kazanılan" value={int(data.son_12_ay?.geri_kazanildi ?? 0)} expected="vadeden sonra yeniden alan" />
        <StatTile
          label="İade oranı"
          value={refunds[1] ? `%${num((100 * refunds[0]) / refunds[1])}` : "—"}
          expected={`${int(refunds[0])} iade / ${int(refunds[1])} satış · 12 ay`}
        />
      </div>

      <div className="table-wrap">
        <table className="board churn-table">
          <thead>
            <tr>
              <th>Vade ayı</th>
              <th className="num">Vadesi dolan</th>
              <th className="num">Yeniledi</th>
              <th className="num">Kaybedildi</th>
              <th className="num">Churn</th>
              <th className="num" title="Vadeden sonra yeniden satın alan">Geri kazanıldı</th>
              <th className="num" title="Vade günü henüz gelmedi">Bekliyor</th>
              <th className="num">Satış</th>
              <th className="num">İade</th>
              <th className="num">İade oranı</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => {
              const future = m.ay > `${new Date().toISOString().slice(0, 7)}-01`;
              return (
                <tr key={m.ay} className={future ? "churn-future" : undefined}>
                  <td className="name">
                    {monthFmt.format(new Date(`${m.ay}T00:00:00Z`))}
                    {future ? <span className="sub"> önümüzdeki</span> : null}
                  </td>
                  <td className="num">{m.vadesi_gelen ? int(m.vadesi_gelen) : <span className="muted">—</span>}</td>
                  <td className="num">{m.yeniledi ? int(m.yeniledi) : "—"}</td>
                  <td className="num">{m.kaybedildi ? <b className="warn-text">{int(m.kaybedildi)}</b> : "—"}</td>
                  <td className="num">{m.churn === null ? <span className="muted">—</span> : <b>{rate(m.churn)}</b>}</td>
                  <td className="num">{m.geri_kazanildi ? int(m.geri_kazanildi) : "—"}</td>
                  <td className="num muted">{m.bekliyor ? int(m.bekliyor) : "—"}</td>
                  <td className="num">{m.satis ? int(m.satis) : "—"}</td>
                  <td className="num">{m.iade ? int(m.iade) : "—"}</td>
                  <td className="num">{rate(m.iade_orani)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted fn-note">
        Churn: aboneliği (satıştan 12 ay) o ay dolan müşterilerden vade gününe kadar yeni abonelik satışı olmayanlar; ek süre
        yok. Oran yalnız vadesi geçmiş olanlar üzerinden (bekleyenler hariç). Abonelik satışı: {data.packages?.join(", ")}.
        Ölçüm CRM'e geçişten (Ekim 2025) itibaren satın alanlar için yapılabiliyor; ilk vadeler Ekim 2026'da doluyor, oran
        Kasım–Aralık 2026'daki büyük vade dalgasıyla oturacak. CRM öncesi müşterilerin Re-Co yenilemeleri bu orana girmez.
      </p>
    </>
  );
}
