import { useMemo, useState } from "react";

import { hourLabel, int, num, pct } from "../format";
import type { HourRow, Kpis } from "../types";

type Metric = "arama" | "ulasilan" | "randevu";

const METRICS: { key: Metric; label: string }[] = [
  { key: "arama", label: "Arama" },
  { key: "ulasilan", label: "Ulaşılan" },
  { key: "randevu", label: "Toplantı" },
];

const W = 720;
const H = 220;
const PAD = { top: 12, right: 8, bottom: 26, left: 36 };
const BAR_MAX = 24;

function niceMax(v: number): number {
  if (v <= 5) return 5;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

/** Bar: 4px yuvarlak uç, tabanda kare. */
function barPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(4, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

export function HourChart({ hours }: { hours: HourRow[] }) {
  const [metric, setMetric] = useState<Metric>("arama");
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    const vals = hours.flatMap((h) => [h.today[metric] ?? 0, h.expected[metric] ?? 0]);
    const max = niceMax(Math.max(1, ...vals));
    const innerW = W - PAD.left - PAD.right;
    const innerH = H - PAD.top - PAD.bottom;
    const band = innerW / Math.max(hours.length, 1);
    const barW = Math.min(BAR_MAX, band * 0.55);
    const y = (v: number) => PAD.top + innerH - (v / max) * innerH;
    const ticks = [0, max / 4, max / 2, (3 * max) / 4, max];
    return { max, band, barW, y, ticks, innerH };
  }, [hours, metric]);

  const active = hover !== null ? hours[hover] : null;

  return (
    <div className="chart">
      <div className="controls" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <div className="legend">
          <span className="key">
            <span className="sw" /> Bugün
          </span>
          <span className="key">
            <span className="sw soft" /> Süren saat
          </span>
          <span className="key">
            <span className="ln" /> 90 gün aynı saat ortalaması
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
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Saatlik ${metric}`}>
        {geo.ticks.map((t) => (
          <g key={t}>
            <line
              className={t === 0 ? "baseline" : "gridline"}
              x1={PAD.left}
              x2={W - PAD.right}
              y1={geo.y(t)}
              y2={geo.y(t)}
            />
            <text className="tick" x={PAD.left - 8} y={geo.y(t) + 4} textAnchor="end">
              {int(t)}
            </text>
          </g>
        ))}
        {hours.map((h, i) => {
          const cx = PAD.left + geo.band * i + geo.band / 2;
          const v = h.today[metric] ?? 0;
          const e = h.expected[metric] ?? 0;
          const top = geo.y(v);
          return (
            <g key={h.hour}>
              <rect
                className="hit"
                x={cx - geo.band / 2}
                y={PAD.top}
                width={geo.band}
                height={geo.innerH}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
              />
              <path
                className={h.done ? "bar" : "bar partial"}
                d={barPath(cx - geo.barW / 2, top, geo.barW, geo.y(0) - top)}
                pointerEvents="none"
              />
              {e > 0 ? (
                <line
                  className="ref"
                  x1={cx - geo.barW / 2 - 5}
                  x2={cx + geo.barW / 2 + 5}
                  y1={geo.y(e)}
                  y2={geo.y(e)}
                  pointerEvents="none"
                />
              ) : null}
              <text className="tick" x={cx} y={H - 8} textAnchor="middle">
                {String(h.hour).padStart(2, "0")}
              </text>
            </g>
          );
        })}
      </svg>
      {active && hover !== null ? (
        <HourTooltip
          row={active}
          left={`${((PAD.left + geo.band * hover + geo.band / 2) / W) * 100}%`}
          top={`${(geo.y(Math.max(active.today[metric] ?? 0, active.expected[metric] ?? 0)) / H) * 100}%`}
        />
      ) : null}
    </div>
  );
}

function HourTooltip({ row, left, top }: { row: HourRow; left: string; top: string }) {
  const t: Kpis = row.today;
  const e: Kpis = row.expected;
  return (
    <div className="tooltip" style={{ left, top }}>
      <div className="t-head">
        {hourLabel(row.hour)} – {hourLabel(row.hour + 1)}
        {row.done ? "" : " · sürüyor"}
      </div>
      <div className="t-row">
        Arama <span><b>{int(t.arama)}</b> / {num(e.arama)}</span>
      </div>
      <div className="t-row">
        Ulaşılan <span><b>{int(t.ulasilan)}</b> / {num(e.ulasilan)}</span>
      </div>
      <div className="t-row">
        Ulaşma oranı <span><b>{pct(t.ulasma_orani)}</b> / {pct(e.ulasma_orani)}</span>
      </div>
      <div className="t-row">
        Toplantı <span><b>{int(t.randevu)}</b> / {num(e.randevu)}</span>
      </div>
      <div className="t-row">
        Doluluk <span><b>{pct(t.doluluk)}</b> / {pct(e.doluluk)}</span>
      </div>
      <div className="t-row muted" style={{ marginTop: 4 }}>
        bugün / 90 gün ortalaması
      </div>
    </div>
  );
}
