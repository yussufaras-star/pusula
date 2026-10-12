import { useMemo, useState } from "react";

import { int } from "../format";
import type { Portfolio, RepCard, UntouchedLead } from "../types";

const COLLAPSED_ROWS = 8;
const LIST_MAX = 60;

const shortDay = new Intl.DateTimeFormat("tr-TR", { day: "2-digit", month: "2-digit", timeZone: "Europe/Istanbul" });

function waited(hours: number | null): string {
  if (hours === null) return "—";
  if (hours < 48) return `${Math.round(hours)} sa`;
  return `${Math.round(hours / 24)} gün`;
}

/** Statü × temsilci dağılımı; yeni adaylarda 24 saattir dokunulmamışlar vurgulu. */
export function PortfolioBoard({ portfolio, reps }: { portfolio: Portfolio; reps: RepCard[] }) {
  const [expanded, setExpanded] = useState(false);
  const untouchedBy = useMemo(() => {
    const out: Record<string, UntouchedLead[]> = {};
    for (const lead of portfolio.untouched) (out[lead.rep_id] ??= []).push(lead);
    return out;
  }, [portfolio.untouched]);

  const repIds = reps.map((r) => r.rep_id);
  const names = Object.fromEntries(reps.map((r) => [r.rep_id, r.name]));
  const cell = new Map(portfolio.by_status.map((r) => [`${r.status}|${r.rep_id}`, r.count]));
  const totals = new Map<string, number>();
  for (const r of portfolio.by_status) totals.set(r.status, (totals.get(r.status) ?? 0) + r.count);
  const statuses = [...totals.keys()].sort((a, b) =>
    a === portfolio.new_status ? -1 : b === portfolio.new_status ? 1 : (totals.get(b) ?? 0) - (totals.get(a) ?? 0),
  );
  const shown = expanded ? statuses : statuses.slice(0, COLLAPSED_ROWS);

  const worst = [...repIds].sort((a, b) => (untouchedBy[b]?.length ?? 0) - (untouchedBy[a]?.length ?? 0))[0];
  const [selected, setSelected] = useState<string | null>(null);
  const active = selected ?? (untouchedBy[worst]?.length ? worst : null);
  const list = active ? (untouchedBy[active] ?? []) : [];
  const single = repIds.length === 1;

  return (
    <>
      <div className="table-wrap">
        <table className="board portfolio">
          <thead>
            <tr>
              <th>Statü</th>
              {repIds.map((id) => (
                <th key={id} className="num">
                  {single ? "Lead" : names[id]}
                </th>
              ))}
              {single ? null : <th className="num">Toplam</th>}
            </tr>
          </thead>
          <tbody>
            {shown.map((status) => {
              const isNew = status === portfolio.new_status;
              return (
                <tr key={status} className={isNew ? "pf-new" : undefined}>
                  <td className="name">{status}</td>
                  {repIds.map((id) => {
                    const n = cell.get(`${status}|${id}`) ?? 0;
                    const u = isNew ? (untouchedBy[id]?.length ?? 0) : 0;
                    return (
                      <td key={id} className="num">
                        {n ? int(n) : <span className="muted">—</span>}
                        {u ? (
                          <button
                            className={`pf-alert${active === id ? " on" : ""}`}
                            onClick={() => setSelected(id)}
                            title={`${portfolio.untouched_hours} saattir dokunulmamış; listeyi göster`}
                          >
                            ⚠ {u}
                          </button>
                        ) : null}
                      </td>
                    );
                  })}
                  {single ? null : <td className="num">{int(totals.get(status) ?? 0)}</td>}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {statuses.length > COLLAPSED_ROWS ? (
        <button className="link-btn" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Daha az statü" : `Tüm statüler (${statuses.length})`}
        </button>
      ) : null}

      {active && list.length ? (
        <div className="pf-list">
          <div className="pf-list-head">
            <b>{single ? "Dokunulmamış yeni adayların" : `${names[active]}`}</b> · {list.length} yeni aday{" "}
            {portfolio.untouched_hours} saattir dokunulmamış
          </div>
          <div className="table-wrap">
            <table className="board">
              <thead>
                <tr>
                  <th>Lead</th>
                  <th>Son temas</th>
                  <th className="num">Bekleme</th>
                </tr>
              </thead>
              <tbody>
                {list.slice(0, LIST_MAX).map((lead) => (
                  <tr key={lead.lead_id}>
                    <td className="name">{lead.name ?? "İsimsiz lead"}</td>
                    <td>
                      {lead.last_touch ? (
                        <>
                          {shortDay.format(new Date(lead.last_touch))}
                          <span className="sub"> · statü güncellenmemiş</span>
                        </>
                      ) : (
                        <span className="warn-text">
                          hiç temas yok
                          {lead.assigned_at ? ` · atanma ${shortDay.format(new Date(lead.assigned_at))}` : ""}
                        </span>
                      )}
                    </td>
                    <td className="num">{waited(lead.hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.length > LIST_MAX ? <div className="muted">…ve {list.length - LIST_MAX} lead daha</div> : null}
        </div>
      ) : portfolio.untouched.length === 0 ? (
        <div className="empty">Bütün yeni adaylara son {portfolio.untouched_hours} saatte dokunulmuş.</div>
      ) : null}
    </>
  );
}
