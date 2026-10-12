import { useMemo, useState } from "react";

import { hourLabel, int, num, pct } from "../format";
import type { ComparePoint, CompareSide, Kpis } from "../types";

type Metric = "arama" | "ulasilan" | "randevu" | "ulasma_orani" | "doluluk";

const METRICS: { key: Metric; label: string; rate?: boolean }[] = [
  { key: "arama", label: "Arama" },
  { key: "ulasilan", label: "Ulaşılan" },
  { key: "randevu", label: "Randevu" },
  { key: "ulasma_orani", label: "Ulaşma", rate: true },
  { key: "doluluk", label: "Doluluk", rate: true },
];

const W = 720;
const H = 240;
const PAD = { top: 14, right: 28, bottom: 28, left: 40 };

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", weekday: "short", timeZone: "UTC" });

function niceMax(v: number): number {
  if (v <= 5) return 5;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

function bucketLabel(p: ComparePoint): string {
  return typeof p.bucket === "number" ? hourLabel(p.bucket) : dayFmt.format(new Date(`${p.bucket}T12:00:00Z`));
}

function axisLabel(p: ComparePoint, i: number): string {
  return typeof p.bucket === "number" ? String(p.bucket).padStart(2, "0") : `${i + 1}`;
}

const read = (p: ComparePoint | undefined, key: Metric): number | null =>
  p ? ((p.kpis[key as keyof Kpis] as number | null) ?? null) : null;

export function CompareChart({ a, b, titleA, titleB }: { a: CompareSide; b: CompareSide; titleA: string; titleB: string }) {
  const [metric, setMetric] = useState<Metric>("arama");
  const [hover, setHover] = useState<number | null>(null);
  const rate = METRICS.find((m) => m.key === metric)?.rate ?? false;
  const fmt = (v: number | null) => (v === null ? "—" : rate ? pct(v) : Number.isInteger(v) ? int(v) : num(v));
  const hourly = a.series_group === "hour" && b.series_group === "hour";

  // Saatlikte iki taraf aynı saate, günlükte sıra numarasına (1. gün, 2. gün…) hizalanır.
  const slots = useMemo(() => {
    if (hourly) {
      const hours = [...new Set([...a.series, ...b.series].map((p) => p.bucket as number))].sort((x, y) => x - y);
      return hours.map((h) => ({
        a: a.series.find((p) => p.bucket === h),
        b: b.series.find((p) => p.bucket === h),
      }));
    }
    const n = Math.max(a.series.length, b.series.length);
    return Array.from({ length: n }, (_, i) => ({ a: a.series[i], b: b.series[i] }));
  }, [a, b, hourly]);

  const geo = useMemo(() => {
    const vals = slots.flatMap((s) => [read(s.a, metric) ?? 0, read(s.b, metric) ?? 0]);
    const max = rate ? 100 : niceMax(Math.max(1, ...vals));
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const step = slots.length > 1 ? innerW / (slots.length - 1) : 0;
    const x = (i: number) => (slots.length > 1 ? PAD.left + step * i : PAD.left + innerW / 2);
    const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
    const ticks = [0, max / 4, max / 2, (3 * max) / 4, max];
    const band = slots.length > 1 ? step : innerW;
    return { x, y, ticks, band, innerH };
  }, [slots, metric, rate]);

  function path(side: "a" | "b"): string {
    let d = "";
    let pen = false;
    slots.forEach((s, i) => {
      const v = read(s[side], metric);
      if (v === null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${geo.x(i).toFixed(1)},${geo.y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }

  const labelEvery = Math.ceil(slots.length / 12);
  const active = hover !== null ? slots[hover] : null;

  if (slots.length === 0) return <div className="empty">Bu dönemde mesai günü yok.</div>;

  return (
    <div className="chart">
      <div className="controls" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <div className="legend">
          <span className="key">
            <span className="sw" style={{ background: "var(--series-1)" }} /> A · {titleA}
          </span>
          <span className="key">
            <span className="sw" style={{ background: "var(--series-2)" }} /> B · {titleB}
          </span>
        </div>
        <div className="controls" role="tablist" aria-label="Ölçü">
          {METRICS.map((m) => (
            <button
              key={m.key}
              className="btn"
              role="tab"
              aria-selected={metric === m.key}
              style={metric === m.key ? { background: "var(--surface-2)", fontWeight: 600 } : undefined}
              onClick={() => setMetric(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${titleA} ile ${titleB} karşılaştırması`}>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line className={t === 0 ? "baseline" : "gridline"} x1={PAD.left} x2={W - PAD.right} y1={geo.y(t)} y2={geo.y(t)} />
            <text className="tick" x={PAD.left - 8} y={geo.y(t) + 4} textAnchor="end">
              {rate ? `%${int(t)}` : int(t)}
            </text>
          </g>
        ))}
        {slots.map((s, i) =>
          i % labelEvery === 0 ? (
            <text key={i} className="tick" x={geo.x(i)} y={H - 8} textAnchor="middle">
              {axisLabel((s.a ?? s.b)!, i)}
            </text>
          ) : null,
        )}
        {hover !== null ? (
          <line className="crosshair" x1={geo.x(hover)} x2={geo.x(hover)} y1={PAD.top} y2={PAD.top + geo.innerH} />
        ) : null}
        {(["b", "a"] as const).map((side) => (
          <g key={side} className={`series ${side}`}>
            <path d={path(side)} />
            {slots.map((s, i) => {
              const v = read(s[side], metric);
              if (v === null) return null;
              const partial = s[side]?.partial;
              const last = i === slots.length - 1 || read(slots[i + 1]?.[side], metric) === null;
              return (
                <g key={i}>
                  {hover === i || slots.length <= 12 || partial ? (
                    <circle className={partial ? "pt partial" : "pt"} cx={geo.x(i)} cy={geo.y(v)} r={4} />
                  ) : null}
                  {last ? (
                    <text className="end-label" x={geo.x(i) + 8} y={geo.y(v) + 4}>
                      {side.toUpperCase()}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
        ))}
        {slots.map((_, i) => (
          <rect
            key={i}
            className="hit"
            x={geo.x(i) - geo.band / 2}
            y={PAD.top}
            width={geo.band}
            height={geo.innerH}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>
      {active && hover !== null ? (
        <div
          className="tooltip"
          style={{
            left: `${(geo.x(hover) / W) * 100}%`,
            top: `${(geo.y(Math.max(read(active.a, metric) ?? 0, read(active.b, metric) ?? 0)) / H) * 100}%`,
          }}
        >
          <div className="t-head">{hourly ? bucketLabel((active.a ?? active.b)!) : `${hover + 1}. mesai günü`}</div>
          <div className="t-row">
            <span>
              <i className="swatch a" /> A{!hourly && active.a ? ` · ${bucketLabel(active.a)}` : ""}
            </span>
            <b>{fmt(read(active.a, metric))}</b>
          </div>
          <div className="t-row">
            <span>
              <i className="swatch b" /> B{!hourly && active.b ? ` · ${bucketLabel(active.b)}` : ""}
            </span>
            <b>{fmt(read(active.b, metric))}</b>
          </div>
          {active.a?.partial || active.b?.partial ? <div className="t-row muted">sürüyor</div> : null}
        </div>
      ) : null}
    </div>
  );
}
