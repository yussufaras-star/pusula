import type { ReactNode } from "react";

import type { Tone } from "../format";

const ICON: Record<Tone, string> = {
  good: "▲",
  warning: "●",
  critical: "▼",
  neutral: "–",
};

export function ToneBadge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`badge ${tone}`}>
      <span className="ico" aria-hidden>
        {ICON[tone]}
      </span>
      {children}
    </span>
  );
}

interface Props {
  label: string;
  value: ReactNode;
  unit?: string;
  expected?: ReactNode;
  tone?: Tone;
  badge?: ReactNode;
  title?: string;
}

export function StatTile({ label, value, unit, expected, tone = "neutral", badge, title }: Props) {
  return (
    <div className="card tile" title={title}>
      <div className="label">{label}</div>
      <div className="value">
        {value}
        {unit ? <small>{unit}</small> : null}
      </div>
      <div className="foot">
        <span>{expected}</span>
        {badge ? <ToneBadge tone={tone}>{badge}</ToneBadge> : null}
      </div>
    </div>
  );
}
