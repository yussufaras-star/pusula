import { useState } from "react";

import { usePolling } from "../api";

// Yönetici özetleri (satış ve finans): sabah işi hesaplar, kaydeder ve Cliq kanalına
// görsel kart olarak gönderir; burada kaydedilen kartların aynısı.

interface Summary {
  day: string;
  html: string;
  sent_to: string | null;
  sent_at: string | null;
}

const dayFmt = new Intl.DateTimeFormat("tr-TR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });
const sentFmt = new Intl.DateTimeFormat("tr-TR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });
const fmtDay = (iso: string) => dayFmt.format(new Date(`${iso.slice(0, 10)}T12:00:00Z`));

export function CeoSummaryPage({
  endpoint = "/api/ceo-summary",
  title = "Yönetici özeti",
  sub = "Her sabah son iş gününün özeti Cliq'te #headquarters kanalına gider · ciro ve yeni müşteri tüm şirket, huni satış ekibi",
}: {
  endpoint?: string;
  title?: string;
  sub?: string;
}) {
  const { data, error } = usePolling<{ summaries: Summary[] }>(endpoint, 10 * 60_000);
  const [index, setIndex] = useState(0);
  const rows = data?.summaries ?? [];
  const s = rows[index];

  return (
    <div className="ceo-page">
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <div className="sub">{sub}</div>
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
        <section className="card">
          <div className="card-head">
            <h2>Cliq'e giden kart</h2>
            <span className="hint">
              {s.sent_at ? `${sentFmt.format(new Date(s.sent_at))}'de ${s.sent_to} adresine gönderildi` : "gönderilmedi"}
            </span>
          </div>
          <div className="card-body ceo-frame-wrap">
            <iframe className="ceo-frame" title={`${title} kartı`} srcDoc={s.html} sandbox="" />
          </div>
        </section>
      ) : null}
    </div>
  );
}
