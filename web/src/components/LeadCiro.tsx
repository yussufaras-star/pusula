import { useState } from "react";

import { usePolling } from "../api";
import { int, num, tl, tlShort } from "../format";
import { StatTile } from "./StatTile";
import type { LeadCiroGran, LeadCiroPayload, LeadCiroPoint } from "../types";

const GRANS: { key: LeadCiroGran; label: string; unit: string }[] = [
  { key: "gun", label: "Günlük", unit: "gün" },
  { key: "hafta", label: "Haftalık", unit: "hafta" },
  { key: "ay", label: "Aylık", unit: "ay" },
  { key: "yil", label: "Yıllık", unit: "yıl" },
];

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const d = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

function tickLabel(p: LeadCiroPoint, gran: LeadCiroGran): string {
  if (gran === "ay") return p.etiket.split(" ")[0].slice(0, 3);
  if (gran === "yil") return p.etiket;
  return d(p.bas).replace(".", "");
}

function periodLabel(p: LeadCiroPoint, gran: LeadCiroGran): string {
  if (gran === "gun") return d(p.bas);
  if (gran === "hafta") return `${d(p.bas)} – ${d(p.bit)}`;
  return p.etiket;
}

const fmtR = (r: number | null | undefined) => (r === null || r === undefined ? "—" : num(r).replace(/^-/, "−"));

const W = 640;
const PANEL = 120;
const GAP = 22;
const PAD = { top: 18, right: 8, bottom: 26, left: 52 };
const H = PAD.top + PANEL + GAP + PANEL + PAD.bottom;

/** Aynı zaman ekseninde iki panel: üstte lead adedi, altta ciro (tek eksen kuralı: iki ölçü iki grafik). */
function TimePanels({ points, gran }: { points: LeadCiroPoint[]; gran: LeadCiroGran }) {
  const [hover, setHover] = useState<number | null>(null);
  const iw = W - PAD.left - PAD.right;
  const slot = iw / points.length;
  const bw = Math.max(1, Math.min(28, slot * 0.7));
  const maxLead = niceMax(Math.max(...points.map((p) => p.lead)));
  const maxCiro = niceMax(Math.max(...points.map((p) => p.ciro)));
  const top1 = PAD.top;
  const top2 = PAD.top + PANEL + GAP;
  const y1 = (v: number) => top1 + PANEL - (v / maxLead) * PANEL;
  const y2 = (v: number) => top2 + PANEL - (v / maxCiro) * PANEL;
  const every = Math.max(1, Math.ceil(points.length / 12));
  const h = hover === null ? null : points[hover];

  return (
    <div className="chart lc-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Yeni lead ve ciro, aynı dönemler" onMouseLeave={() => setHover(null)}>
        <text x={PAD.left} y={top1 - 6} className="tick lc-panel-title">Yeni lead</text>
        <text x={PAD.left} y={top2 - 6} className="tick lc-panel-title">Ciro (Re-Co hariç)</text>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y1(t * maxLead)} y2={y1(t * maxLead)} className="gridline" />
            <text x={PAD.left - 6} y={y1(t * maxLead) + 4} textAnchor="end" className="tick">{int(t * maxLead)}</text>
            <line x1={PAD.left} x2={W - PAD.right} y1={y2(t * maxCiro)} y2={y2(t * maxCiro)} className="gridline" />
            <text x={PAD.left - 6} y={y2(t * maxCiro) + 4} textAnchor="end" className="tick">
              {tlShort(t * maxCiro).replace(" TL", "")}
            </text>
          </g>
        ))}
        {points.map((p, i) => {
          const x = PAD.left + slot * i + (slot - bw) / 2;
          const on = hover === i;
          const cls = `${p.suruyor ? " partial" : ""}${on ? " on" : ""}`;
          return (
            <g key={p.bas} onMouseEnter={() => setHover(i)}>
              <rect x={PAD.left + slot * i} y={top1} width={slot} height={H - top1 - PAD.bottom} fill="transparent" />
              <rect x={x} y={y1(p.lead)} width={bw} height={Math.max(top1 + PANEL - y1(p.lead), 0)} rx={Math.min(3, bw / 2)} className={`lc-lead${cls}`} />
              <rect x={x} y={y2(p.ciro)} width={bw} height={Math.max(top2 + PANEL - y2(p.ciro), 0)} rx={Math.min(3, bw / 2)} className={`ciro-bar${cls}`} />
              {i % every === (points.length - 1) % every ? (
                <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="tick">{tickLabel(p, gran)}</text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {h ? (
        <div className="tooltip" style={{ left: `${((PAD.left + slot * (hover ?? 0) + slot / 2) / W) * 100}%`, top: `${(y1(h.lead) / H) * 100}%` }}>
          <div className="t-head">{periodLabel(h, gran)}{h.suruyor ? " · sürüyor" : ""}</div>
          <div className="t-row">Yeni lead <b>{int(h.lead)}</b></div>
          <div className="t-row">Ciro <b>{tl(h.ciro)}</b></div>
          <div className="t-row">Satış <b>{int(h.satis)}</b></div>
          <div className="t-row">Lead başı ciro <b>{h.ciro_per_lead === null ? "—" : tlShort(h.ciro_per_lead)}</b></div>
        </div>
      ) : null}
    </div>
  );
}

const SW = 300;
const SH = 230;
const SP = { top: 12, right: 10, bottom: 34, left: 52 };

/** Dağılım: her nokta tamamlanmış bir dönem (x lead, y ciro) + en küçük kareler doğrusu. */
function Scatter({ points, gran }: { points: LeadCiroPoint[]; gran: LeadCiroGran }) {
  const [hover, setHover] = useState<number | null>(null);
  const done = points.filter((p) => !p.suruyor);
  const maxX = niceMax(Math.max(...done.map((p) => p.lead)));
  const maxY = niceMax(Math.max(...done.map((p) => p.ciro)));
  const iw = SW - SP.left - SP.right;
  const ih = SH - SP.top - SP.bottom;
  const x = (v: number) => SP.left + (v / maxX) * iw;
  const y = (v: number) => SP.top + ih - (v / maxY) * ih;
  const n = done.length;
  const mx = done.reduce((a, p) => a + p.lead, 0) / n;
  const my = done.reduce((a, p) => a + p.ciro, 0) / n;
  const sxx = done.reduce((a, p) => a + (p.lead - mx) ** 2, 0);
  const slope = sxx ? done.reduce((a, p) => a + (p.lead - mx) * (p.ciro - my), 0) / sxx : 0;
  const fit = (v: number) => my + slope * (v - mx);
  const lo = Math.min(...done.map((p) => p.lead));
  const hi = Math.max(...done.map((p) => p.lead));
  const h = hover === null ? null : done[hover];

  return (
    <div className="chart lc-scatter">
      <svg viewBox={`0 0 ${SW} ${SH}`} role="img" aria-label="Lead adedi ile ciro dağılımı" onMouseLeave={() => setHover(null)}>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={SP.left} x2={SW - SP.right} y1={y(t * maxY)} y2={y(t * maxY)} className="gridline" />
            <text x={SP.left - 6} y={y(t * maxY) + 4} textAnchor="end" className="tick">{tlShort(t * maxY).replace(" TL", "")}</text>
            <text x={x(t * maxX)} y={SH - SP.bottom + 14} textAnchor="middle" className="tick">{int(t * maxX)}</text>
          </g>
        ))}
        <text x={SP.left + iw / 2} y={SH - 4} textAnchor="middle" className="tick">yeni lead / {GRANS.find((g) => g.key === gran)?.unit}</text>
        {n >= 2 && sxx ? (
          <line x1={x(lo)} x2={x(hi)} y1={y(Math.max(fit(lo), 0))} y2={y(Math.max(fit(hi), 0))} className="ref lc-fit" />
        ) : null}
        {done.map((p, i) => (
          <circle
            key={p.bas}
            cx={x(p.lead)}
            cy={y(p.ciro)}
            r={hover === i ? 6 : 4.5}
            className={`lc-dot${hover === i ? " on" : ""}`}
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>
      {h ? (
        <div className="tooltip" style={{ left: `${(x(h.lead) / SW) * 100}%`, top: `${(y(h.ciro) / SH) * 100}%` }}>
          <div className="t-head">{periodLabel(h, gran)}</div>
          <div className="t-row">Yeni lead <b>{int(h.lead)}</b></div>
          <div className="t-row">Ciro <b>{tl(h.ciro)}</b></div>
        </div>
      ) : null}
    </div>
  );
}

function rTone(r: number | null | undefined): "good" | "warning" | "neutral" {
  if (r === null || r === undefined) return "neutral";
  return r >= 0.4 ? "good" : r <= -0.2 ? "warning" : "neutral";
}

/** Yeni lead (Zoho oluşma günü) ↔ ciro: dönem serisi, dağılım, gecikmeli korelasyon. */
export function LeadCiro() {
  const [gran, setGran] = useState<LeadCiroGran>("hafta");
  const { data, error } = usePolling<LeadCiroPayload>(`/api/lead-ciro?gran=${gran}`, 0);
  const unit = GRANS.find((g) => g.key === gran)?.unit ?? "";

  const chips = (
    <div className="chips trend-chips" role="group" aria-label="Dönem">
      {GRANS.map((g) => (
        <button key={g.key} className={`chip${gran === g.key ? " on" : ""}`} onClick={() => setGran(g.key)} aria-pressed={gran === g.key}>
          {g.label}
        </button>
      ))}
    </div>
  );
  if (error) return <>{chips}<div className="banner error">Lead ↔ ciro verisi alınamadı: {error.message}</div></>;
  if (!data || data.gran !== gran) return <>{chips}<div className="skeleton" /></>;
  if (!data.available || !data.seri) return <>{chips}<div className="empty">Lead oluşma zamanları ilk saatlik güncellemeyle gelecek.</div></>;

  const best = data.en_iyi;
  const lagged = best && best.gecikme > 0 && best.r != null && data.r != null && best.r - data.r >= 0.1;
  const measured = (data.gecikmeler ?? []).filter((g) => g.r !== null);

  return (
    <>
      {chips}
      <div className="tiles">
        <StatTile
          label={`Korelasyon (aynı ${unit})`}
          value={fmtR(data.r)}
          expected={data.r === null ? `en az ${data.min_nokta} tamamlanmış ${unit} gerekir` : `r · ${int(data.n)} ${unit}`}
          tone={rTone(data.r)}
          badge={data.yorum ? (data.yorum === "yok" ? "ilişki yok" : data.yorum) : undefined}
          title="Pearson r: +1 lead arttıkça ciro da artıyor, 0 ilişki yok, −1 ters yönde. Süren dönem hesaba girmez."
        />
        <StatTile
          label="En güçlü gecikme"
          value={best ? (best.gecikme ? `${best.gecikme} ${unit} sonra` : "aynı dönem") : "—"}
          expected={best ? `r ${fmtR(best.r)}${best.yorum && best.yorum !== "yok" ? ` · ${best.yorum}` : ""}` : "yetersiz veri"}
          title="Bu dönemin lead'i ile N dönem sonraki ciro arasındaki korelasyon; lead'in satışa dönme süresini gösterir."
        />
        <StatTile
          label="Lead başı ciro"
          value={data.lead_basi_ciro === null || data.lead_basi_ciro === undefined ? "—" : tlShort(data.lead_basi_ciro)}
          expected="tamamlanmış dönemler toplamı"
        />
      </div>

      <div className="lc-grid">
        <TimePanels points={data.seri} gran={gran} />
        {data.seri.filter((p) => !p.suruyor).length >= 2 ? (
          <Scatter points={data.seri} gran={gran} />
        ) : (
          <div className="empty">Dağılım için en az iki tamamlanmış {unit} gerekir.</div>
        )}
      </div>

      {measured.length > 1 ? (
        <div className="lc-lags" aria-label="Gecikmeli korelasyon">
          <span className="muted">Gecikmeli r (lead → sonraki ciro):</span>
          {measured.map((g) => (
            <span key={g.gecikme} className={`lc-lag${best && g.gecikme === best.gecikme ? " best" : ""}`} title={`${int(g.n)} ${unit}`}>
              {g.gecikme ? `+${g.gecikme}` : "0"}: {fmtR(g.r)}
            </span>
          ))}
        </div>
      ) : null}

      <p className="muted fn-note">
        Lead: Zoho'da oluşturulduğu gün (sonradan kişiye dönüştürülenler dahil). Ciro: kazanılan satışlar, Re-Co yenilemeleri
        hariç (mevcut müşteriden gelir). Açık renkli dönem sürüyor; korelasyona girmez.{" "}
        {lagged
          ? `Lead'in etkisi cirodan ${best?.gecikme} ${unit} sonra daha net görünüyor.`
          : "Korelasyon ilişkiyi gösterir, nedeni değil."}{" "}
        {gran === "gun" ? "Son 90 gün; pazar günleri satış olmadığı için günlük r gürültülüdür." : null}
        {gran === "yil" ? "Veri Ekim 2025'te başlar; yıllıkta korelasyon için yeterli nokta yok." : null}
      </p>
    </>
  );
}
