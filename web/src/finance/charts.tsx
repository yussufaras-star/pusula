import { useState } from "react";

import { COMPANY_SHORT, companyColor, dayS, month, niceScale, short, usd } from "./format";
import type { CompanyId, Overview, ProjWeek } from "./types";

// Çizim genişliği; tam genişlik kartlarda daha geniş verilir ki yazı büyümesin.
const NARROW = 720;
export const WIDE = 1280;
const PAD = { top: 16, right: 12, bottom: 26, left: 56 };

function scale(lo: number, hi: number, h: number) {
  const ih = h - PAD.top - PAD.bottom;
  return (v: number) => PAD.top + ih - ((v - lo) / (hi - lo || 1)) * ih;
}

function Grid({ ticks, y, W }: { ticks: number[]; y: (v: number) => number; W: number }) {
  return (
    <>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className={t === 0 ? "baseline" : "gridline"} />
          <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="tick">
            {short(t)}
          </text>
        </g>
      ))}
    </>
  );
}

export function CompanyKey({ id }: { id: CompanyId }) {
  return (
    <span className="key">
      <span className="sw" style={{ background: companyColor(id) }} />
      {COMPANY_SHORT[id]}
    </span>
  );
}

// --- bakiye seyri ------------------------------------------------------------------

/** Şirket başına USD bakiye çizgisi; birden çok şirkette toplam da çizilir. */
export function BalanceChart({ history, ids }: { history: Overview["history"]; ids: CompanyId[] }) {
  const W = NARROW;
  const [hover, setHover] = useState<number | null>(null);
  if (history.length < 2) return <div className="empty">Veri yetersiz.</div>;
  const H = 240;
  const showTotal = ids.length > 1;
  const values = history.flatMap((p) => [...ids.map((c) => p[c] ?? 0), showTotal ? p.total : 0]);
  const axis = niceScale(Math.min(...values), Math.max(...values));
  const y = scale(axis.lo, axis.hi, H);
  const iw = W - PAD.left - PAD.right;
  const x = (i: number) => PAD.left + (i / (history.length - 1)) * iw;
  const path = (get: (i: number) => number) => history.map((_, i) => `${i ? "L" : "M"}${x(i)},${y(get(i))}`).join("");
  const every = Math.max(1, Math.ceil(history.length / 8));
  const h = hover === null ? null : history[hover];

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label="Nakit seyri"
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const px = ((e.clientX - box.left) / box.width) * W;
          const i = Math.round(((px - PAD.left) / iw) * (history.length - 1));
          setHover(Math.max(0, Math.min(history.length - 1, i)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        <Grid ticks={axis.ticks} y={y} W={W} />
        {history.map((p, i) =>
          i % every === (history.length - 1) % every ? (
            <text key={p.day} x={x(i)} y={H - 8} textAnchor="middle" className="tick">
              {dayS(p.day)}
            </text>
          ) : null,
        )}
        {ids.map((c) => (
          <path key={c} d={path((i) => history[i][c] ?? 0)} fill="none" stroke={companyColor(c)} strokeWidth={2} strokeLinejoin="round" />
        ))}
        {showTotal ? (
          <path d={path((i) => history[i].total)} fill="none" className="fin-total" strokeWidth={2} strokeDasharray="5 4" />
        ) : null}
        {hover !== null ? (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={H - PAD.bottom} className="crosshair" />
            {ids.map((c) => (
              <circle key={c} cx={x(hover)} cy={y(history[hover][c] ?? 0)} r={4} fill={companyColor(c)} className="ring" />
            ))}
          </>
        ) : null}
      </svg>
      {h && hover !== null ? (
        <div className="tooltip" style={{ left: `${(x(hover) / W) * 100}%`, top: `${(PAD.top / H) * 100}%`, transform: "translate(-50%, 0)" }}>
          <div className="t-head">{dayS(h.day)}</div>
          {ids.map((c) => (
            <div key={c} className="t-row">
              <span>{COMPANY_SHORT[c]}</span>
              <b>{usd(h[c] ?? 0)}</b>
            </div>
          ))}
          {showTotal ? (
            <div className="t-row">
              <span>Toplam</span>
              <b>{usd(h.total)}</b>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="legend fin-legend">
        {ids.map((c) => (
          <CompanyKey key={c} id={c} />
        ))}
        {showTotal ? (
          <span className="key">
            <span className="ln dashed" />
            Toplam
          </span>
        ) : null}
      </div>
    </div>
  );
}

// --- aylık giriş / çıkış -----------------------------------------------------------------

interface FlowBar {
  key: string;
  label: string;
  in: number;
  out: number;
  net?: number;
  line?: number;
}

/** Giriş sıfırın üstünde, çıkış altında; tek eksen. İsteğe bağlı kapanış çizgisi. */
function InOutChart({
  bars,
  height,
  floor,
  lineLabel,
  tooltipTitle,
  onPick,
  W = NARROW,
}: {
  W?: number;
  bars: FlowBar[];
  height: number;
  floor?: number;
  lineLabel?: string;
  tooltipTitle: (b: FlowBar) => string;
  onPick?: (i: number) => void;
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!bars.length) return <div className="empty">Veri yetersiz.</div>;
  const lines = bars.map((b) => b.line).filter((v): v is number => v !== undefined);
  const axis = niceScale(
    Math.min(...bars.map((b) => -b.out), ...lines, floor ?? 0),
    Math.max(...bars.map((b) => b.in), ...lines, floor ?? 0),
  );
  const y = scale(axis.lo, axis.hi, height);
  const iw = W - PAD.left - PAD.right;
  const slot = iw / bars.length;
  const bw = Math.min(28, slot * 0.6);
  const cx = (i: number) => PAD.left + slot * i + slot / 2;
  const every = Math.max(1, Math.ceil(bars.length / 13));
  const h = hover === null ? null : bars[hover];

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label="Giriş ve çıkış" onMouseLeave={() => setHover(null)}>
        <Grid ticks={axis.ticks} y={y} W={W} />
        {floor !== undefined && floor !== 0 ? (
          <g>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(floor)} y2={y(floor)} className="fin-floor" />
            <text x={W - PAD.right} y={y(floor) - 4} textAnchor="end" className="tick">
              alt limit
            </text>
          </g>
        ) : null}
        {bars.map((b, i) => (
          <g key={b.key} onMouseEnter={() => setHover(i)} onClick={() => onPick?.(i)} style={onPick ? { cursor: "pointer" } : undefined}>
            <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={height - PAD.top - PAD.bottom} className="hit" />
            {b.in > 0 ? (
              <rect x={cx(i) - bw / 2} y={y(b.in)} width={bw} height={Math.max(y(0) - y(b.in) - 1, 0)} rx={3} className="fin-in" />
            ) : null}
            {b.out > 0 ? (
              <rect x={cx(i) - bw / 2} y={y(0) + 1} width={bw} height={Math.max(y(-b.out) - y(0) - 1, 0)} rx={3} className="fin-out" />
            ) : null}
            {i % every === (bars.length - 1) % every ? (
              <text x={cx(i)} y={height - 8} textAnchor="middle" className="tick">
                {b.label}
              </text>
            ) : null}
          </g>
        ))}
        {lines.length === bars.length ? (
          <>
            <path d={bars.map((b, i) => `${i ? "L" : "M"}${cx(i)},${y(b.line ?? 0)}`).join("")} fill="none" className="fin-total" strokeWidth={2} />
            {bars.map((b, i) => (
              <circle key={b.key} cx={cx(i)} cy={y(b.line ?? 0)} r={hover === i ? 5 : 3} className={`fin-dot${floor !== undefined && (b.line ?? 0) < floor ? " low" : ""}`} />
            ))}
          </>
        ) : null}
      </svg>
      {h && hover !== null ? (
        <div className="tooltip" style={{ left: `${(cx(hover) / W) * 100}%`, top: `${(PAD.top / height) * 100}%`, transform: "translate(-50%, 0)" }}>
          <div className="t-head">{tooltipTitle(h)}</div>
          <div className="t-row">
            <span>Giriş</span>
            <b>{usd(h.in)}</b>
          </div>
          <div className="t-row">
            <span>Çıkış</span>
            <b>{usd(h.out)}</b>
          </div>
          {h.net !== undefined ? (
            <div className="t-row">
              <span>Net</span>
              <b>{usd(h.net)}</b>
            </div>
          ) : null}
          {h.line !== undefined ? (
            <div className="t-row">
              <span>{lineLabel}</span>
              <b>{usd(h.line)}</b>
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="legend fin-legend">
        <span className="key">
          <span className="sw fin-in-sw" />
          Giriş
        </span>
        <span className="key">
          <span className="sw fin-out-sw" />
          Çıkış
        </span>
        {lineLabel ? (
          <span className="key">
            <span className="ln" />
            {lineLabel}
          </span>
        ) : null}
        {floor ? (
          <span className="key">
            <span className="ln dashed critical" />
            Alt limit
          </span>
        ) : null}
      </div>
    </div>
  );
}

export function MonthlyFlowChart({ months }: { months: { month: string; in: number; out: number; net: number }[] }) {
  return (
    <InOutChart
      height={240}
      bars={months.map((m) => ({ key: m.month, label: month(m.month), in: m.in, out: m.out, net: m.net }))}
      tooltipTitle={(b) => month(b.key)}
    />
  );
}

export function ProjectionChart({ weeks, floor, onPick }: { weeks: ProjWeek[]; floor: number; onPick?: (i: number) => void }) {
  return (
    <InOutChart
      W={WIDE}
      height={300}
      floor={floor}
      lineLabel="Hafta sonu kasa"
      onPick={onPick}
      bars={weeks.map((w) => ({ key: w.week, label: dayS(w.week), in: w.in, out: w.out, line: w.close }))}
      tooltipTitle={(b) => `${dayS(b.key)} haftası`}
    />
  );
}
