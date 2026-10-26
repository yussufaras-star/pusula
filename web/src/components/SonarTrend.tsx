import { useEffect, useMemo, useRef, useState } from "react";

import { usePolling } from "../api";
import { ShareButton } from "./ShareButton";

// SONAR-41 gelişimi: haftalık toplam puan ortalaması (1-5), tek grafik. Dikey işaret koçluğun
// başladığı hafta; temsilci seçiliyse ekip çizgisi kıyas için yanında durur.

interface TrendWeek {
  week_start: string;
  n: number;
  puan_ort: number | null;
}

interface Trend {
  kocluk_baslangic: string;
  weeks: TrendWeek[];
}

const RANGES: [number, string][] = [
  [0, "Analiz başından beri"],
  [8, "Son 8 hafta"],
  [12, "Son 12 hafta"],
];

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const fmtDay = (iso: string) => dayFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));
const f2 = (v: number) => v.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const key = (iso: string) => iso.slice(0, 10);

interface Series {
  label: string;
  cls: "a" | "b";
  byWeek: Map<string, TrendWeek>;
}

function TrendChart({ weeks, series, marker }: { weeks: string[]; series: Series[]; marker: string }) {
  const [hover, setHover] = useState<number | null>(null);
  // Gerçek genişlikte çizilir: yazılar her ekranda aynı boyda kalır.
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(720);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(280, Math.round(el.clientWidth))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const H = 260;
  const PAD = { top: 16, right: 64, bottom: 26, left: 34 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const n = weeks.length;
  const step = n > 1 ? innerW / (n - 1) : 0;
  const x = (i: number) => (n > 1 ? PAD.left + step * i : PAD.left + innerW / 2);
  // Puanlama tablosunun basamakları: 1 kaçırıldı · 2 denendi · 3 doğru · 4 hedef · 5 örnek.
  const y = (v: number) => PAD.top + innerH - ((v - 1) / 4) * innerH;
  const mi = weeks.indexOf(key(marker));
  const labelEvery = Math.ceil(n / 8);
  const val = (s: Series, i: number) => s.byWeek.get(weeks[i])?.puan_ort ?? null;

  const path = (s: Series) => {
    let d = "";
    let pen = false;
    weeks.forEach((_, i) => {
      const v = val(s, i);
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };

  return (
    <div className="chart sonar-mini" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="SONAR-41 haftalık puan ortalaması">
        {[1, 2, 3, 4, 5].map((t) => (
          <g key={t}>
            <line
              className={t === 1 ? "baseline" : t === 4 ? "gridline sonar-target" : "gridline"}
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(t)}
              y2={y(t)}
            />
            <text className="tick" x={PAD.left - 8} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        <text className="tick sonar-target-label" x={W - PAD.right + 6} y={y(4) + 4}>
          hedef
        </text>
        {weeks.map((w, i) =>
          i % labelEvery === 0 || i === n - 1 ? (
            <text key={w} className="tick" x={x(i)} y={H - 6} textAnchor="middle">
              {fmtDay(w)}
            </text>
          ) : null,
        )}
        {mi >= 0 ? (
          <g className="sonar-marker">
            <line x1={x(mi)} x2={x(mi)} y1={PAD.top} y2={PAD.top + innerH} />
            <text x={x(mi) + 4} y={PAD.top + 10}>
              koçluk başladı
            </text>
          </g>
        ) : null}
        {hover !== null ? <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} /> : null}
        {[...series].reverse().map((s) => {
          let lastI = -1;
          weeks.forEach((_, i) => {
            if (val(s, i) !== null) lastI = i;
          });
          const lastV = lastI >= 0 ? val(s, lastI) : null;
          return (
            <g key={s.label} className={`series ${s.cls}`}>
              <path d={path(s)} />
              {weeks.map((_, i) => {
                const v = val(s, i);
                return v === null ? null : <circle key={i} className="pt" cx={x(i)} cy={y(v)} r={hover === i ? 5 : 4} />;
              })}
              {lastV !== null ? (
                <text className="end-label" x={x(lastI) + 8} y={y(lastV) + 4}>
                  {f2(lastV)}
                </text>
              ) : null}
            </g>
          );
        })}
        {weeks.map((w, i) => (
          <rect
            key={w}
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
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%`, top: `${(PAD.top / H) * 100}%` }}>
          <div className="t-head">{fmtDay(weeks[hover])} haftası</div>
          {series.map((s) => {
            const w = s.byWeek.get(weeks[hover]);
            return (
              <div key={s.label} className="t-row">
                <span>
                  <i className={`swatch ${s.cls}`} /> {s.label}
                </span>
                <b>{w?.puan_ort == null ? "—" : `${f2(w.puan_ort)} · ${w.n} görüşme`}</b>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export function SonarTrend({ rep, scope, isAdmin }: { rep: string; scope: string; isAdmin: boolean }) {
  const [open, setOpen] = useState(true);
  const [range, setRange] = useState(0);
  const team = usePolling<Trend>("/api/conversations/trend", 10 * 60_000);
  const own = usePolling<Trend>(rep ? `/api/conversations/trend?rep=${rep}` : "/api/conversations/trend", 10 * 60_000);
  const data = rep ? own.data : team.data;

  const view = useMemo(() => {
    if (!data) return null;
    const all = [...new Set([...(team.data?.weeks ?? []), ...data.weeks].map((w) => key(w.week_start)))].sort();
    const weeks = range ? all.slice(-range) : all;
    const map = (t: Trend | null | undefined) => new Map((t?.weeks ?? []).map((w) => [key(w.week_start), w]));
    const series: Series[] = rep
      ? [
          { label: scope || "Temsilci", cls: "a", byWeek: map(own.data) },
          { label: "Ekip", cls: "b", byWeek: map(team.data) },
        ]
      : [{ label: "Ekip", cls: "a", byWeek: map(team.data) }];
    return { weeks, series };
  }, [data, team.data, own.data, range, rep, scope]);

  if (!data || !view || !view.weeks.length) return null;
  const start = key(data.kocluk_baslangic);
  const main = view.series[0].byWeek;
  const base = main.get(start)?.puan_ort ?? null;
  const lastWeek = [...main.keys()].sort().pop() ?? null;
  const last = lastWeek && lastWeek !== start ? (main.get(lastWeek)?.puan_ort ?? null) : null;
  const delta = base !== null && last !== null ? last - base : null;

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
          SONAR-41 gelişimi{scope ? ` · ${scope}` : " · ekip"}
        </h2>
        <span className="hint">
          koçluğun başladığı hafta ({fmtDay(start)}) {base === null ? "—" : f2(base)}
          {last !== null && delta !== null && lastWeek
            ? ` → son hafta (${fmtDay(lastWeek)}) ${f2(last)} (${delta >= 0 ? "+" : "−"}${f2(Math.abs(delta))})`
            : " · karşılaştırma için sonraki hafta bekleniyor"}
        </span>
        <span className="sonar-head-actions" onClick={(e) => e.stopPropagation()}>
          <select className="f-input sm" data-share-skip value={range} onChange={(e) => setRange(Number(e.target.value))} aria-label="Dönem">
            {RANGES.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          {isAdmin && open ? <ShareButton kart="sonar-gelisim" baslik={`SONAR-41 gelişimi${scope ? ` · ${scope}` : " · ekip"}`} /> : null}
        </span>
      </div>
      {open ? (
        <div className="card-body">
          {view.series.length > 1 ? (
            <div className="legend" style={{ marginBottom: 6 }}>
              {view.series.map((s) => (
                <span key={s.label} className="key">
                  <span className="sw" style={{ background: s.cls === "a" ? "var(--series-1)" : "var(--series-2)" }} /> {s.label}
                </span>
              ))}
            </div>
          ) : null}
          <TrendChart weeks={view.weeks} series={view.series} marker={start} />
          <p className="muted sonar-note">
            Haftalık toplam SONAR-41 puanı: o hafta analiz edilen görüşmelerin 5 boyut ortalaması (1 kaçırıldı · 2 denendi · 3
            doğru · 4 hedef · 5 örnek). Dikey çizgi koçluğun başladığı hafta; yeşil kesik çizgi hedef seviye (4).
            {view.weeks.length < 4 ? ` Şu an ${view.weeks.length} haftalık veri var; eğilim için en az 4 hafta gerekir.` : ""}
          </p>
        </div>
      ) : null}
    </section>
  );
}
