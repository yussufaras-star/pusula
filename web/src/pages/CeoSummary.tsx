import { useState } from "react";

import { usePolling } from "../api";
import { StatTile } from "../components/StatTile";
import { int, tlShort } from "../format";

// CEO özeti: sabah işi hesaplar, kaydeder ve CEO'ya Cliq'ten gönderir; burada kaydedilenler.

interface Summary {
  day: string;
  payload: {
    day: string;
    son: { satis: number; ciro: number };
    ay: {
      ad: string;
      satis: number;
      ciro: number;
      hedef: { satis: number; ciro: number } | null;
      tempo_satis: number | null;
      tempo_ciro: number | null;
      is_gunu: number;
      toplam_is_gunu: number;
    };
    huni: Record<string, number | null>;
    huni_kiyas: Record<string, number | null>;
    bugun_toplanti: number;
    istisnalar: string[];
  };
  body: string;
  sent_to: string | null;
  sent_at: string | null;
}

const FUNNEL: [string, string][] = [
  ["gelen_lead", "Gelen lead"],
  ["arama", "Arama"],
  ["ulasilan", "Ulaşılan"],
  ["randevu", "Toplantı planlanan"],
  ["katildi", "Toplantıya gelen"],
  ["satis", "Yeni müşteri"],
];

const dayFmt = new Intl.DateTimeFormat("tr-TR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
const sentFmt = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });
const fmtDay = (iso: string) => dayFmt.format(new Date(`${iso.slice(0, 10)}T12:00:00Z`));
const share = (a: number, b: number) => (b ? `%${Math.round((a / b) * 100)}` : "—");

export function CeoSummaryPage() {
  const { data, error } = usePolling<{ summaries: Summary[] }>("/api/ceo-summary", 10 * 60_000);
  const [index, setIndex] = useState(0);
  const rows = data?.summaries ?? [];
  const s = rows[index];

  return (
    <div className="ceo-page">
      <div className="page-head">
        <div>
          <h1>CEO özeti</h1>
          <div className="sub">
            Her sabah son iş gününün özeti Mehmet Tek'e Cliq'ten gider · ciro ve yeni müşteri tüm şirket, huni satış ekibi
          </div>
        </div>
        {rows.length ? (
          <div className="controls">
            <select className="f-input sm" value={index} onChange={(e) => setIndex(Number(e.target.value))} aria-label="Gün">
              {rows.map((r, i) => (
                <option key={r.day} value={i}>
                  {fmtDay(r.day)}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>
      {error ? <div className="banner error">Özet alınamadı: {error.message}</div> : null}
      {data && !s ? <div className="card empty">Henüz özet yok; ilki sabah işinde oluşur.</div> : null}
      {s ? (
        <>
          <div className="tiles">
            <StatTile
              label={`Son iş günü · ${fmtDay(s.payload.day)}`}
              value={int(s.payload.son.satis)}
              unit="yeni müşteri"
              expected={`${tlShort(s.payload.son.ciro)} ciro`}
            />
            <StatTile
              label={`${s.payload.ay.ad} · yeni müşteri`}
              value={int(s.payload.ay.satis)}
              unit={s.payload.ay.hedef ? `/ ${int(s.payload.ay.hedef.satis)}` : undefined}
              expected={
                s.payload.ay.tempo_satis !== null
                  ? `tempo ay sonu ~${int(Math.round(s.payload.ay.tempo_satis))}${s.payload.ay.hedef ? ` · ${share(s.payload.ay.satis, s.payload.ay.hedef.satis)}` : ""}`
                  : undefined
              }
              tone={
                s.payload.ay.hedef && s.payload.ay.tempo_satis !== null
                  ? s.payload.ay.tempo_satis >= s.payload.ay.hedef.satis
                    ? "good"
                    : "warning"
                  : "neutral"
              }
            />
            <StatTile
              label={`${s.payload.ay.ad} · ciro`}
              value={tlShort(s.payload.ay.ciro)}
              expected={
                s.payload.ay.tempo_ciro !== null
                  ? `tempo ay sonu ~${tlShort(s.payload.ay.tempo_ciro)}${s.payload.ay.hedef ? ` · hedef ${tlShort(s.payload.ay.hedef.ciro)}` : ""}`
                  : undefined
              }
              tone={
                s.payload.ay.hedef && s.payload.ay.tempo_ciro !== null
                  ? s.payload.ay.tempo_ciro >= s.payload.ay.hedef.ciro
                    ? "good"
                    : "warning"
                  : "neutral"
              }
            />
            <StatTile label="Bugün planlı toplantı" value={int(s.payload.bugun_toplanti)} />
          </div>
          <section className="card">
            <div className="card-head">
              <h2>Huni · {fmtDay(s.payload.day)}</h2>
              <span className="hint">parantezde son 4 haftanın aynı gününün ortalaması</span>
            </div>
            <div className="card-body">
              <div className="table-wrap">
                <table className="board">
                  <tbody>
                    {FUNNEL.filter(([k]) => s.payload.huni[k] !== null && s.payload.huni[k] !== undefined).map(([k, label]) => (
                      <tr key={k}>
                        <td>{label}</td>
                        <td className="num">
                          <b>{int(s.payload.huni[k] ?? 0)}</b>
                        </td>
                        <td className="num muted">{s.payload.huni_kiyas[k] !== null ? `ort. ${int(Math.round(s.payload.huni_kiyas[k] ?? 0))}` : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p>
                <strong>Ekip:</strong> {s.payload.istisnalar.length ? s.payload.istisnalar.join("; ") : "normal, sapan temsilci yok"}
              </p>
            </div>
          </section>
          <section className="card">
            <div className="card-head">
              <h2>Cliq mesajı</h2>
              <span className="hint">
                {s.sent_at ? `${sentFmt.format(new Date(s.sent_at))}'de ${s.sent_to} adresine gönderildi` : "gönderilmedi"}
              </span>
            </div>
            <div className="card-body">
              <pre className="ceo-body">{s.body}</pre>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
