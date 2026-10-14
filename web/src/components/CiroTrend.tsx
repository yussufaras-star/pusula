import { useState } from "react";

import { int, num, tl, tlShort } from "../format";
import type { CiroTrend as Trend, ReportRow, ToDate, TrendPoint } from "../types";

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const d = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));

/** % değişim rozeti: artış yeşil ▲, düşüş kırmızı ▼. */
export function Change({ v, muted }: { v: number | null | undefined; muted?: boolean }) {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  const tone = v > 0 ? "good" : v < 0 ? "critical" : "neutral";
  return (
    <span className={`chg ${tone}${muted ? " soft" : ""}`}>
      {v > 0 ? "▲" : v < 0 ? "▼" : "–"} %{num(Math.abs(v))}
    </span>
  );
}

function Compare({ label, t }: { label: string; t: ToDate }) {
  return (
    <div className="trend-cmp" title={`Önceki: ${d(t.onceki_bas)} – ${d(t.onceki_bit)}`}>
      <div className="trend-cmp-label">{label}</div>
      <div className="trend-cmp-val">
        {tlShort(t.ciro)} <Change v={t.degisim} />
      </div>
      <div className="muted trend-cmp-sub">
        önceki {tlShort(t.onceki_ciro)} · {int(t.satis)} / {int(t.onceki_satis)} satış
      </div>
    </div>
  );
}

const W = 440;
const H = 230;
const PAD = { top: 26, right: 8, bottom: 28, left: 52 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

function label(p: TrendPoint, weekly: boolean): string {
  if (weekly && p.bas) return d(p.bas).replace(".", "");
  return (p.ay_etiket ?? "").split(" ")[0].slice(0, 3);
}

function TrendChart({ points, weekly }: { points: TrendPoint[]; weekly: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) return <div className="empty">Satış yok.</div>;
  const max = niceMax(Math.max(...points.map((p) => p.ciro)));
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const slot = iw / points.length;
  const bw = Math.min(36, slot * 0.62);
  const y = (v: number) => PAD.top + ih - (v / max) * ih;
  const h = hover === null ? null : points[hover];
  const every = weekly ? 2 : 1;

  return (
    <div className="chart ciro-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={weekly ? "Haftalık ciro" : "Aylık ciro"} onMouseLeave={() => setHover(null)}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t * max)} y2={y(t * max)} className="gridline" />
            <text x={PAD.left - 6} y={y(t * max) + 4} textAnchor="end" className="tick">
              {tlShort(t * max).replace(" TL", "")}
            </text>
          </g>
        ))}
        {points.map((p, i) => {
          const x = PAD.left + slot * i + (slot - bw) / 2;
          const top = y(p.ciro);
          const ch = p.degisim;
          return (
            <g key={p.bas ?? p.ay} onMouseEnter={() => setHover(i)}>
              <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={ih} fill="transparent" />
              <rect
                x={x}
                y={top}
                width={bw}
                height={Math.max(PAD.top + ih - top, 0)}
                rx={3}
                className={`ciro-bar${p.suruyor ? " partial" : ""}${hover === i ? " on" : ""}`}
              />
              {ch !== null && !p.suruyor ? (
                <text x={x + bw / 2} y={top - 6} textAnchor="middle" className={`chg-tick ${ch >= 0 ? "up" : "down"}`}>
                  {ch >= 0 ? "+" : "−"}
                  {Math.round(Math.abs(ch))}%
                </text>
              ) : null}
              {i % every === (points.length - 1) % every ? (
                <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="tick">
                  {label(p, weekly)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {h ? (
        <div
          className="tooltip"
          style={{ left: `${((PAD.left + slot * (hover ?? 0) + slot / 2) / W) * 100}%`, top: `${(y(h.ciro) / H) * 100}%` }}
        >
          <b>{weekly && h.bas && h.bit ? `${d(h.bas)} – ${d(h.bit)}` : h.ay_etiket}</b>
          <div>{tl(h.ciro)}</div>
          <div className="muted">
            {int(h.satis)} satış {h.degisim !== null ? <>· önceki {weekly ? "haftaya" : "aya"} <Change v={h.degisim} /></> : null}
          </div>
          {h.suruyor ? <div className="muted">sürüyor; kıyas için üstteki "aynı güne kadar"a bak</div> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Ekip ciro trendi: aynı güne kadar kıyas + haftalık/aylık seri. */
export function CiroTrendCard({ trend }: { trend: Trend }) {
  const [mode, setMode] = useState<"hafta" | "ay">("hafta");
  const weekly = mode === "hafta";
  return (
    <>
      <div className="trend-cmps">
        <Compare label="Bu hafta · geçen haftanın aynı gününe kadar" t={trend.ayni_gune.hafta} />
        <Compare label="Bu ay · geçen ayın aynı gününe kadar" t={trend.ayni_gune.ay} />
      </div>
      <div className="chips trend-chips" role="group" aria-label="Seri">
        {(["hafta", "ay"] as const).map((m) => (
          <button key={m} className={`chip${mode === m ? " on" : ""}`} onClick={() => setMode(m)} aria-pressed={mode === m}>
            {m === "hafta" ? "Haftalık" : "Aylık"}
          </button>
        ))}
      </div>
      <TrendChart points={weekly ? trend.haftalar : trend.aylar} weekly={weekly} />
      <p className="muted fn-note">
        Çubuk üstündeki %: bir önceki {weekly ? "haftaya" : "aya"} göre değişim. Açık renkli çubuk süren dönem; onu tam
        dönemle değil, üstteki "aynı güne kadar" kıyasıyla değerlendir.{" "}
        {weekly ? "Hafta pazartesi başlar." : "Ay toplamı ileri tarihli kapanışları da sayar."}
      </p>
    </>
  );
}

/** Temsilci bazında aynı güne kadar ciro değişimi + seçilen dönemin payı. */
export function RepCiroChange({ trend, reps, total, ranked }: { trend: Trend; reps: ReportRow[]; total: number; ranked: boolean }) {
  const rows = reps
    .map((r) => ({ r, t: trend.temsilci[r.rep_id ?? ""] }))
    .filter((x) => x.t)
    .sort((a, b) => b.t.ay.ciro - a.t.ay.ciro);
  if (!rows.length) return null;
  const team = trend.ayni_gune;
  return (
    <div className="table-wrap">
      <table className="board rep-chg">
        <thead>
          <tr>
            <th>Temsilci</th>
            <th className="num">Bu hafta</th>
            <th className="num">Geçen hafta</th>
            <th className="num">Değişim</th>
            <th className="num">Bu ay</th>
            <th className="num">Geçen ay</th>
            <th className="num">Değişim</th>
            {ranked ? <th className="num" title="Seçilen dönemdeki ekip cirosu içindeki pay">Dönem payı</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ r, t }) => (
            <tr key={r.rep_id}>
              <td className="name">{r.name}</td>
              <td className="num">{tlShort(t.hafta.ciro)}</td>
              <td className="num muted">{tlShort(t.hafta.onceki_ciro)}</td>
              <td className="num"><Change v={t.hafta.degisim} /></td>
              <td className="num">{tlShort(t.ay.ciro)}</td>
              <td className="num muted">{tlShort(t.ay.onceki_ciro)}</td>
              <td className="num"><Change v={t.ay.degisim} /></td>
              {ranked ? <td className="num">{total > 0 ? `%${num((100 * r.ciro) / total)}` : "—"}</td> : null}
            </tr>
          ))}
        </tbody>
        {ranked ? (
          <tfoot>
            <tr>
              <td className="name">Ekip</td>
              <td className="num">{tlShort(team.hafta.ciro)}</td>
              <td className="num muted">{tlShort(team.hafta.onceki_ciro)}</td>
              <td className="num"><Change v={team.hafta.degisim} /></td>
              <td className="num">{tlShort(team.ay.ciro)}</td>
              <td className="num muted">{tlShort(team.ay.onceki_ciro)}</td>
              <td className="num"><Change v={team.ay.degisim} /></td>
              <td className="num">%100</td>
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}
