import { num, pct, tl } from "../format";
import type { ReportRow } from "../types";

interface Stage {
  key: keyof ReportRow;
  label: string;
  rate?: keyof ReportRow;
  rateLabel?: string;
}

const STAGES: Stage[] = [
  { key: "lead_payda", label: "Aranan lead" },
  { key: "lead_pay", label: "Ulaşılan lead", rate: "ulasma_orani", rateLabel: "ulaşma" },
  { key: "randevu", label: "Toplantı", rate: "toplanti_donus", rateLabel: "ulaşılandan toplantı" },
  { key: "katildi", label: "Katılan toplantı", rate: "katilim_orani", rateLabel: "katılım" },
  { key: "satis", label: "Satış", rate: "satis_orani", rateLabel: "ulaşılandan satış" },
];

const value = (row: ReportRow, key: keyof ReportRow): number | null => {
  const v = row[key];
  return typeof v === "number" ? v : null;
};

function RateDiff({ a, b }: { a: number | null; b: number | null }) {
  if (a === null || b === null) return null;
  const d = a - b;
  if (Math.abs(d) < 0.05) return <span className="fn-diff">±0</span>;
  return (
    <span className={`fn-diff ${d > 0 ? "up" : "down"}`}>
      {d > 0 ? "▲" : "▼"} {num(Math.abs(d))} puan
    </span>
  );
}

/** İki tarafın hunisi alt alta çubuklarla; adım oranları ve fark puan olarak. */
export function Funnel({ a, b, labelA, labelB }: { a: ReportRow; b: ReportRow; labelA: string; labelB: string }) {
  const top = Math.max(value(a, "lead_payda") ?? 0, value(b, "lead_payda") ?? 0, 1);
  const bar = (row: ReportRow, key: keyof ReportRow, cls: string, label: string) => {
    const v = value(row, key) ?? 0;
    return (
      <div className="fn-bar-row" title={`${label}: ${num(v)}`}>
        <div className="fn-track">
          <div className={`fn-bar ${cls}`} style={{ width: `max(3px, ${(v / top) * 100}%)` }} />
        </div>
        <span className="fn-val tabular">{num(v)}</span>
      </div>
    );
  };

  return (
    <div className="funnel">
      <div className="legend">
        <span>
          <span className="swatch a" /> {labelA}
        </span>
        <span>
          <span className="swatch b" /> {labelB}
        </span>
      </div>
      {STAGES.map((stage) => (
        <div key={stage.key} className="fn-stage">
          <div className="fn-head">
            <span className="fn-label">{stage.label}</span>
            {stage.rate ? (
              <span className="fn-rate">
                {stage.rateLabel}: <b>{pct(value(a, stage.rate))}</b>
                <span className="muted"> / {pct(value(b, stage.rate))}</span>{" "}
                <RateDiff a={value(a, stage.rate)} b={value(b, stage.rate)} />
              </span>
            ) : null}
          </div>
          {bar(a, stage.key, "fn-a", labelA)}
          {bar(b, stage.key, "fn-b", labelB)}
        </div>
      ))}
      <div className="fn-stage fn-ciro">
        <div className="fn-head">
          <span className="fn-label">Ciro</span>
          <span className="fn-rate">
            ort. satış: <b>{tl(a.ort_satis)}</b>
            <span className="muted"> / {tl(b.ort_satis)}</span>
          </span>
        </div>
        <div className="fn-ciro-vals">
          <span>
            <span className="swatch a" /> <b className="tabular">{tl(a.ciro)}</b>
          </span>
          <span>
            <span className="swatch b" /> <b className="tabular">{tl(b.ciro)}</b>
          </span>
        </div>
      </div>
    </div>
  );
}
