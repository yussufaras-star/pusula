import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { Change } from "./CiroTrend";
import { LeadChannels } from "./LeadChannels";
import { StatTile } from "./StatTile";
import type { LeadFlowPayload, LeadFlowWeek } from "../types";

const WEEKS = [8, 12, 26] as const;
/** Temsilci tablosunda gösterilen son hafta sütunu (26 haftada tablo taşmasın). */
const TABLE_WEEKS = 8;

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const d = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));
const short = (iso: string) => d(iso).replace(".", "");

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

/** "%4,8 arttı" / "%1,7 azaldı": toplantıda okunacak cümle için. */
function moved(v: number | null | undefined): string {
  if (v === null || v === undefined) return "kıyas yok";
  if (Math.abs(v) < 0.5) return "değişmedi";
  return `%${num(Math.abs(v))} ${v > 0 ? "arttı" : "azaldı"}`;
}

function tone(v: number | null | undefined): "good" | "critical" | "neutral" {
  if (v === null || v === undefined || Math.abs(v) < 0.5) return "neutral";
  return v > 0 ? "good" : "critical";
}

const badge = (v: number | null | undefined) =>
  v === null || v === undefined ? undefined : `%${num(Math.abs(v))}`;

const W = 440;
const H = 220;
const PAD = { top: 26, right: 8, bottom: 28, left: 40 };

/** Haftalık çubuk; çubuk üstünde önceki haftaya göre %. Süren hafta açık renkli. */
function WeekBars({
  weeks,
  value,
  delta,
  fmt,
  cls,
  label,
  detail,
}: {
  weeks: LeadFlowWeek[];
  value: (w: LeadFlowWeek) => number | null;
  delta: (w: LeadFlowWeek) => number | null;
  fmt: (v: number) => string;
  cls: string;
  label: string;
  detail: (w: LeadFlowWeek) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const max = niceMax(Math.max(...weeks.map((w) => value(w) ?? 0)));
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const slot = iw / weeks.length;
  const bw = Math.min(36, slot * 0.62);
  const y = (v: number) => PAD.top + ih - (v / max) * ih;
  const every = Math.max(1, Math.ceil(weeks.length / 8));
  // 26 haftada her çubuğa % yazmak okunmaz; etiket seyrekleşir.
  const showTicks = weeks.length <= 13;
  const h = hover === null ? null : weeks[hover];

  return (
    <div className="chart ciro-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} onMouseLeave={() => setHover(null)}>
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t * max)} y2={y(t * max)} className="gridline" />
            <text x={PAD.left - 6} y={y(t * max) + 4} textAnchor="end" className="tick">
              {fmt(t * max)}
            </text>
          </g>
        ))}
        {weeks.map((w, i) => {
          const v = value(w) ?? 0;
          const x = PAD.left + slot * i + (slot - bw) / 2;
          const top = y(v);
          const ch = delta(w);
          return (
            <g key={w.bas} onMouseEnter={() => setHover(i)}>
              <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={ih} fill="transparent" />
              <rect
                x={x}
                y={top}
                width={bw}
                height={Math.max(PAD.top + ih - top, 0)}
                rx={3}
                className={`${cls}${w.suruyor ? " partial" : ""}${hover === i ? " on" : ""}`}
              />
              {showTicks && ch !== null && !w.suruyor ? (
                <text x={x + bw / 2} y={top - 6} textAnchor="middle" className={`chg-tick ${ch >= 0 ? "up" : "down"}`}>
                  {ch >= 0 ? "+" : "−"}
                  {Math.round(Math.abs(ch))}%
                </text>
              ) : null}
              {i % every === (weeks.length - 1) % every ? (
                <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="tick">
                  {short(w.bas)}
                </text>
              ) : null}
            </g>
          );
        })}
      </svg>
      {h ? (
        <div
          className="tooltip"
          style={{ left: `${((PAD.left + slot * (hover ?? 0) + slot / 2) / W) * 100}%`, top: `${(y(value(h) ?? 0) / H) * 100}%` }}
        >
          <b>
            {d(h.bas)} – {d(h.bit)}
            {h.suruyor ? " · sürüyor" : ""}
          </b>
          <div>{detail(h)}</div>
          {delta(h) !== null ? (
            <div className="muted">
              önceki haftaya <Change v={delta(h)} />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Lead akışı: haftalık gelen lead ve temsilci başına günlük lead, % değişim (satış-pazarlama toplantısı). */
export function LeadFlow({ ranked }: { ranked: boolean }) {
  const [weeks, setWeeks] = useState<number>(12);
  const { data, error } = usePolling<LeadFlowPayload>(`/api/lead-flow?weeks=${weeks}`, 0);

  const chips = (
    <div className="chips trend-chips" role="group" aria-label="Hafta sayısı">
      {WEEKS.map((n) => (
        <button key={n} className={`chip${weeks === n ? " on" : ""}`} onClick={() => setWeeks(n)} aria-pressed={weeks === n}>
          {n} hafta
        </button>
      ))}
    </div>
  );
  if (error) return <>{chips}<div className="banner error">Lead akışı alınamadı: {error.message}</div></>;
  if (!data) return <>{chips}<div className="skeleton" /></>;
  if (!data.available || !data.haftalar?.length) return <>{chips}<div className="empty">Gelen lead sayımı henüz yok.</div></>;

  const series = data.haftalar;
  const last = data.son_hafta;
  const td = data.ayni_gune;
  const four = data.dort_hafta;
  const reps = data.temsilciler ?? [];
  const cols = series.slice(-(TABLE_WEEKS + 1));
  const offset = series.length - cols.length;

  return (
    <>
      {chips}
      {last ? (
        <p className="lf-summary">
          Geçen hafta ({d(last.bas)} – {d(last.bit)}) <b>{int(last.gelen)} lead</b> geldi; önceki haftaya göre{" "}
          <b>{moved(last.degisim_gelen)}</b>. Temsilci başına günde <b>{num(last.kisi_gunluk)} lead</b> düştü (
          {moved(last.degisim_kisi_gunluk)}).
          {four ? (
            <>
              {" "}Son 4 haftada haftalık ortalama {int(four.gelen)} lead; önceki 4 haftaya göre <b>{moved(four.degisim_gelen)}</b>.
            </>
          ) : null}
        </p>
      ) : null}
      <div className="tiles">
        <StatTile
          label="Geçen hafta gelen lead"
          value={last ? int(last.gelen) : "—"}
          expected={last ? `günde ${num(last.gunluk_gelen)} · ${d(last.bas)} haftası` : ""}
          tone={tone(last?.degisim_gelen)}
          badge={badge(last?.degisim_gelen)}
          title="Zoho'ya düşen tekil kişi (aynı e-posta/telefon tekrar sayılmaz). Rozet: önceki haftaya göre."
        />
        <StatTile
          label="Temsilci başı günlük lead"
          value={last ? num(last.kisi_gunluk) : "—"}
          expected={last ? `geçen hafta · ${int(last.temsilci)} temsilci` : ""}
          tone={tone(last?.degisim_kisi_gunluk)}
          badge={badge(last?.degisim_kisi_gunluk)}
          title="Satış temsilcisine atanan lead / lead alan temsilci / 7 gün (lead'ler pazar dahil her gün atanır)."
        />
        <StatTile
          label="Bu hafta · aynı güne kadar"
          value={td ? int(td.gelen) : "—"}
          expected={td ? `geçen hafta ${int(td.onceki_gelen)} · ${td.gun} gün` : "pazartesi: tamamlanmış gün yok"}
          tone={tone(td?.degisim_gelen)}
          badge={badge(td?.degisim_gelen)}
          title={
            td
              ? `Gelen lead, ${d(series[series.length - 1].bas)} – ${d(td.bit)} ile ${d(td.onceki_bas)} – ${d(td.onceki_bit)}. Bugün yarım gün olduğu için sayılmaz. Temsilci başı günlük: ${num(td.kisi_gunluk)} (geçen hafta ${num(td.onceki_kisi_gunluk)}).`
              : undefined
          }
        />
        <StatTile
          label="Son 4 hafta · temsilci başı günlük"
          value={four ? num(four.kisi_gunluk) : "—"}
          expected={four ? `önceki 4 hafta ${num(four.onceki_kisi_gunluk)}` : "8 tamamlanmış hafta gerekir"}
          tone={tone(four?.degisim_kisi_gunluk)}
          badge={badge(four?.degisim_kisi_gunluk)}
          title="Tek haftalık dalgalanmayı süzer: son 4 tamamlanmış haftanın ortalaması, önceki 4 haftayla."
        />
      </div>

      <div className="lf-grid">
        <div>
          <div className="lf-chart-title">Gelen lead · haftalık</div>
          <WeekBars
            weeks={series}
            value={(w) => w.gelen}
            delta={(w) => w.degisim_gelen}
            fmt={(v) => int(v)}
            cls="lc-lead"
            label="Haftalık gelen lead"
            detail={(w) => `${int(w.gelen)} lead${w.gunluk_gelen !== null ? ` · günde ${num(w.gunluk_gelen)}` : ""}`}
          />
        </div>
        <div>
          <div className="lf-chart-title">Temsilci başı günlük lead</div>
          <WeekBars
            weeks={series}
            value={(w) => w.kisi_gunluk}
            delta={(w) => w.degisim_kisi_gunluk}
            fmt={(v) => int(v)}
            cls="ciro-bar"
            label="Temsilci başına günlük atanan lead"
            detail={(w) => `günde ${num(w.kisi_gunluk)} lead · ${int(w.atanan)} atama, ${int(w.temsilci)} temsilci`}
          />
        </div>
      </div>

      <LeadChannels weeks={weeks} />

      {reps.length ? <div className="lf-chart-title lf-section">Temsilci başı günlük lead</div> : null}
      {reps.length ? (
        <div className="table-wrap">
          <table className="board lf-reps">
            <thead>
              <tr>
                <th>Temsilci</th>
                {cols.map((w) => (
                  <th key={w.bas} className={`num${w.suruyor ? " muted" : ""}`} title={`${d(w.bas)} – ${d(w.bit)}`}>
                    {w.suruyor ? "Bu hafta" : short(w.bas)}
                  </th>
                ))}
                <th className="num" title="Geçen hafta / önceki hafta">Haftalık</th>
                <th className="num" title="Son 4 tamamlanmış hafta ortalaması">4 hf ort.</th>
                <th className="num" title="Son 4 hafta / önceki 4 hafta">4 hf değişim</th>
              </tr>
            </thead>
            <tbody>
              {reps.map((r) => (
                <tr key={r.rep_id}>
                  <td className="name">{r.name}</td>
                  {cols.map((w, i) => (
                    <td key={w.bas} className={`num${w.suruyor ? " muted" : ""}`}>
                      {num(r.seri[offset + i])}
                    </td>
                  ))}
                  <td className="num"><Change v={r.degisim} /></td>
                  <td className="num">{num(r.ort4)}</td>
                  <td className="num"><Change v={r.degisim_ort4} /></td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td className="name">Ekip ortalaması</td>
                {cols.map((w) => (
                  <td key={w.bas} className={`num${w.suruyor ? " muted" : ""}`}>
                    {num(w.kisi_gunluk)}
                  </td>
                ))}
                <td className="num"><Change v={last?.degisim_kisi_gunluk} /></td>
                <td className="num">{num(four?.kisi_gunluk)}</td>
                <td className="num"><Change v={four?.degisim_kisi_gunluk} /></td>
              </tr>
            </tfoot>
          </table>
        </div>
      ) : null}

      <p className="muted fn-note">
        Gelen: Zoho'ya düşen tekil kişi (Ekip Panosu'yla aynı sayım). Temsilci başı günlük: satış temsilcisine atanan lead ÷
        lead alan temsilci ÷ 7 gün; lead'ler pazar dahil her gün atanır. Çubuk üstündeki %: önceki haftaya göre. Açık renkli
        çubuk süren hafta; onu "aynı güne kadar" kıyasıyla değerlendir (bugün yarım gün olduğu için sayılmaz). Hafta pazartesi
        başlar.{ranked ? "" : " Tabloda yalnız kendi satırın ve ekip ortalaması görünür."}
      </p>
    </>
  );
}
