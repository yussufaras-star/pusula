import { useEffect, useMemo, useRef, useState } from "react";

import { usePolling } from "../api";
import { num, pct } from "../format";
import { ShareButton } from "./ShareButton";

// SONAR-41 gelişimi: haftalık seriler, koçluk başlangıcı (21 Ekim) dikey işaretle.
// Her grafik tek ölçü ve tek eksen; kesik çizgi 3 haftalık hareketli ortalama.

interface TrendWeek {
  week_start: string;
  n: number;
  puan_ort: number | null;
  puanlar: Record<string, number | null>;
  hedef_payi: number | null;
  bug_payi: number | null;
  davranislar: Record<string, number | null>;
  musait_sordu_orani: number | null;
}

interface Trend {
  kocluk_baslangic: string;
  oncesi: { n: number; puan_ort: number | null };
  sonrasi: { n: number; puan_ort: number | null };
  davranis_adlari: Record<string, string>;
  score_labels: Record<string, string>;
  weeks: TrendWeek[];
}

const RANGES: [number, string][] = [
  [0, "Analiz başından beri"],
  [8, "Son 8 hafta"],
  [12, "Son 12 hafta"],
];

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const fmtDay = (iso: string) => dayFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));
const DAY = 86_400_000;

function movingAvg(vals: (number | null)[], k = 3): (number | null)[] {
  return vals.map((_, i) => {
    const win = vals.slice(Math.max(0, i - k + 1), i + 1).filter((v): v is number => v !== null);
    return i >= k - 1 && win.length === k ? win.reduce((a, b) => a + b, 0) / k : null;
  });
}

/** Tek ölçülü küçük çizgi grafik; x = haftalar, dikey işaret = koçluk başlangıcı. */
function MiniLine({
  title,
  weeks,
  values,
  domain,
  fmt,
  marker,
  height = 150,
  showAvg = true,
}: {
  title: string;
  weeks: TrendWeek[];
  values: (number | null)[];
  domain: [number, number];
  fmt: (v: number) => string;
  marker: string;
  height?: number;
  showAvg?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  // Gerçek genişlikte çizilir: yazılar her grafikte aynı boyda kalır.
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(360);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(200, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = height;
  const PAD = { top: 12, right: 14, bottom: 22, left: 34 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const n = weeks.length;
  const step = n > 1 ? innerW / (n - 1) : 0;
  const x = (i: number) => (n > 1 ? PAD.left + step * i : PAD.left + innerW / 2);
  const y = (v: number) => PAD.top + innerH - ((v - domain[0]) / (domain[1] - domain[0])) * innerH;
  const ticks = [domain[0], (domain[0] + domain[1]) / 2, domain[1]];
  const avg = showAvg ? movingAvg(values) : [];

  // Koçluk işareti: başlangıç gününün haftası içindeki konumu.
  const mt = new Date(`${marker}T00:00:00Z`).getTime();
  let markerX: number | null = null;
  weeks.forEach((w, i) => {
    const ws = new Date(`${w.week_start}T00:00:00Z`).getTime();
    if (mt >= ws && mt < ws + 7 * DAY) markerX = x(i) + (step || 0) * ((mt - ws) / (7 * DAY));
  });

  const path = (vs: (number | null)[]) => {
    let d = "";
    let pen = false;
    vs.forEach((v, i) => {
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const labelEvery = Math.ceil(n / 6);
  const hv = hover !== null ? values[hover] : null;

  return (
    <div className="chart sonar-mini" ref={box}>
      <div className="sonar-mini-title">{title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={title}>
        {ticks.map((t) => (
          <g key={t}>
            <line className={t === domain[0] ? "baseline" : "gridline"} x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={PAD.left - 6} y={y(t) + 4} textAnchor="end">
              {fmt(t)}
            </text>
          </g>
        ))}
        {weeks.map((w, i) =>
          i % labelEvery === 0 || i === n - 1 ? (
            <text key={w.week_start} className="tick" x={x(i)} y={H - 6} textAnchor="middle">
              {fmtDay(w.week_start)}
            </text>
          ) : null,
        )}
        {markerX !== null ? (
          <g className="sonar-marker">
            <line x1={markerX} x2={markerX} y1={PAD.top} y2={PAD.top + innerH} />
            <text x={markerX + 4} y={PAD.top + 9}>
              koçluk
            </text>
          </g>
        ) : null}
        {showAvg ? (
          <g className="series ma">
            <path d={path(avg)} />
          </g>
        ) : null}
        <g className="series a">
          <path d={path(values)} />
          {values.map((v, i) =>
            v === null ? null : <circle key={i} className="pt" cx={x(i)} cy={y(v)} r={hover === i ? 5 : 4} />,
          )}
        </g>
        {hover !== null ? <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} /> : null}
        {weeks.map((w, i) => (
          <rect
            key={w.week_start}
            className="hit"
            x={x(i) - (n > 1 ? step / 2 : innerW / 2)}
            y={PAD.top}
            width={n > 1 ? step : innerW}
            height={innerH}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      {hover !== null ? (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%`, top: `${((hv !== null ? y(hv) : PAD.top) / H) * 100}%` }}>
          <div className="t-head">{fmtDay(weeks[hover].week_start)} haftası</div>
          <div className="t-row">
            <span>Değer</span>
            <b>{hv === null ? "—" : fmt(hv)}</b>
          </div>
          {showAvg && avg[hover] !== null ? (
            <div className="t-row">
              <span>3 hafta ort.</span>
              <b>{fmt(avg[hover] as number)}</b>
            </div>
          ) : null}
          <div className="t-row muted">{weeks[hover].n} görüşme</div>
        </div>
      ) : null}
    </div>
  );
}

export function SonarTrend({ rep, scope, isAdmin }: { rep: string; scope: string; isAdmin: boolean }) {
  const [open, setOpen] = useState(true);
  const [range, setRange] = useState(0);
  const { data } = usePolling<Trend>(`/api/conversations/trend${rep ? `?rep=${rep}` : ""}`, 10 * 60_000);
  const weeks = useMemo(() => {
    const all = data?.weeks ?? [];
    return range ? all.slice(-range) : all;
  }, [data, range]);
  if (!data || !data.weeks.length) return null;

  const start = data.kocluk_baslangic;
  const b = data.oncesi.puan_ort;
  const a = data.sonrasi.puan_ort;
  const score = (v: number) => num(Math.round(v * 10) / 10);
  const share = (v: number) => pct(v);

  return (
    <section className="card" data-share="sonar-gelisim">
      <div
        className="card-head clickable"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setOpen((v) => !v)}
      >
        <h2>
          <span className="caret" aria-hidden data-share-skip>
            {open ? "▾" : "▸"}
          </span>{" "}
          SONAR-41 gelişimi{scope ? ` · ${scope}` : ""}
        </h2>
        <span className="hint">
          koçluk başlangıcı {fmtDay(start)} · öncesi ort. {b === null ? "—" : num(b)} ({data.oncesi.n} görüşme) ·
          sonrası {a === null ? "henüz veri yok" : `ort. ${num(a)} (${data.sonrasi.n} görüşme)`}
        </span>
        <span className="sonar-head-actions" onClick={(e) => e.stopPropagation()}>
          <select className="f-input sm" data-share-skip value={range} onChange={(e) => setRange(Number(e.target.value))} aria-label="Dönem">
            {RANGES.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          {isAdmin ? <ShareButton kart="sonar-gelisim" baslik={`SONAR-41 gelişimi${scope ? ` · ${scope}` : ""}`} /> : null}
        </span>
      </div>
      {open ? (
        <div className="card-body">
          {weeks.length < 4 ? (
            <p className="muted sonar-note">
              {weeks.length} haftalık veri var. Eğilim için en az 4 hafta gerekir; dikey çizgi koçluğun başladığı gün, kesik
              çizgi 3 haftalık ortalama (3. haftadan itibaren).
            </p>
          ) : null}
          <div className="sonar-grid two">
            <MiniLine title="Ortalama puan (1-5)" weeks={weeks} values={weeks.map((w) => w.puan_ort)} domain={[1, 5]} fmt={score} marker={start} height={180} />
            <MiniLine
              title="Hedef seviye payı · 4-5 alan davranış (%)"
              weeks={weeks}
              values={weeks.map((w) => w.hedef_payi)}
              domain={[0, 100]}
              fmt={share}
              marker={start}
              height={180}
            />
          </div>
          <h3 className="sonar-sub">Boyutlar</h3>
          <div className="sonar-grid five">
            {Object.entries(data.score_labels).map(([k, label]) => (
              <MiniLine key={k} title={`${k} · ${label}`} weeks={weeks} values={weeks.map((w) => w.puanlar[k] ?? null)} domain={[1, 5]} fmt={score} marker={start} />
            ))}
          </div>
          <h3 className="sonar-sub">İzlenen davranışlar</h3>
          <div className="sonar-grid four">
            {Object.entries(data.davranis_adlari).map(([k, name]) => (
              <MiniLine key={k} title={`${k} · ${name}`} weeks={weeks} values={weeks.map((w) => w.davranislar[k] ?? null)} domain={[1, 5]} fmt={score} marker={start} />
            ))}
            <MiniLine
              title="“Müsait misiniz?” soran görüşme (%) · düşmesi iyi"
              weeks={weeks}
              values={weeks.map((w) => w.musait_sordu_orani)}
              domain={[0, 100]}
              fmt={share}
              marker={start}
            />
          </div>
          <p className="muted sonar-note">
            Dikey çizgi: SONAR-41 koçluğunun başladığı gün ({fmtDay(start)}). Nokta: haftanın değeri (fareyle görüşme sayısı),
            kesik çizgi: 3 haftalık hareketli ortalama. Haftada kişi başı 15 görüşme ölçüldüğü için tek hafta oynak olabilir;
            eğilime bakın.
          </p>
        </div>
      ) : null}
    </section>
  );
}
