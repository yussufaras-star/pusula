import { useMemo, useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import type { ReportRow, StatusSourcesPayload } from "../types";

type Dim = "lead_source" | "utm_source" | "utm_campaign" | "utm_content";
type Scope = "portfoy" | "donem";

const DIMS: { key: Dim; label: string }[] = [
  { key: "lead_source", label: "Lead kaynağı" },
  { key: "utm_source", label: "Reklam kaynağı" },
  { key: "utm_campaign", label: "Kampanya" },
  { key: "utm_content", label: "Reklam içeriği" },
];

const TOP = 7;
const OTHER = "Diğer";
const EMPTY = "(boş)";

/** Satırdaki pay → 0..4 ton (tek renk, açıktan koyuya). */
function heat(share: number): number {
  if (share <= 0) return 0;
  if (share < 0.1) return 1;
  if (share < 0.25) return 2;
  if (share < 0.5) return 3;
  return 4;
}

export function StatusSources({
  start,
  end,
  reps,
  isAdmin,
}: {
  start: string;
  end: string;
  reps: ReportRow[];
  isAdmin: boolean;
}) {
  const [dim, setDim] = useState<Dim>("lead_source");
  const [scope, setScope] = useState<Scope>("portfoy");
  const [rep, setRep] = useState<string>("");
  const q = new URLSearchParams({ dim });
  if (rep) q.set("rep_id", rep);
  if (scope === "donem") {
    q.set("start", start);
    q.set("end", end);
  }
  const { data, error } = usePolling<StatusSourcesPayload>(`/api/status-sources?${q}`, 0);

  const pivot = useMemo(() => {
    if (!data?.available) return null;
    const bySource = new Map<string, number>();
    for (const r of data.rows) if (r.source) bySource.set(r.source, (bySource.get(r.source) ?? 0) + r.count);
    const top = [...bySource.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP).map(([k]) => k);
    const topSet = new Set(top);
    const cols = [...top];
    const cells = new Map<string, Map<string, number>>();
    const rowTotal = new Map<string, number>();
    for (const r of data.rows) {
      const col = r.source === null ? EMPTY : topSet.has(r.source) ? r.source : OTHER;
      const row = cells.get(r.status) ?? new Map<string, number>();
      row.set(col, (row.get(col) ?? 0) + r.count);
      cells.set(r.status, row);
      rowTotal.set(r.status, (rowTotal.get(r.status) ?? 0) + r.count);
    }
    if (data.rows.some((r) => r.source !== null && !topSet.has(r.source))) cols.push(OTHER);
    if (data.rows.some((r) => r.source === null)) cols.push(EMPTY);
    const statuses = [...rowTotal.keys()].sort((a, b) => (rowTotal.get(b) ?? 0) - (rowTotal.get(a) ?? 0));
    const colTotal = new Map(cols.map((c) => [c, statuses.reduce((s, st) => s + (cells.get(st)?.get(c) ?? 0), 0)]));
    const grand = statuses.reduce((s, st) => s + (rowTotal.get(st) ?? 0), 0);
    return { cols, statuses, cells, rowTotal, colTotal, grand };
  }, [data]);

  return (
    <>
      <div className="ss-controls">
        <div className="chips" role="group" aria-label="Kaynak alanı">
          {DIMS.map((d) => (
            <button key={d.key} className={`chip${dim === d.key ? " on" : ""}`} onClick={() => setDim(d.key)} aria-pressed={dim === d.key}>
              {d.label}
            </button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="Kapsam">
          <button className={`chip${scope === "portfoy" ? " on" : ""}`} onClick={() => setScope("portfoy")} aria-pressed={scope === "portfoy"}>
            Tüm portföy
          </button>
          <button className={`chip${scope === "donem" ? " on" : ""}`} onClick={() => setScope("donem")} aria-pressed={scope === "donem"}>
            Dönemde gelenler
          </button>
        </div>
        {isAdmin ? (
          <select className="ss-rep" value={rep} onChange={(e) => setRep(e.target.value)} aria-label="Temsilci">
            <option value="">Tüm ekip</option>
            {reps.map((r) => (
              <option key={r.rep_id} value={r.rep_id}>
                {r.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {error ? <div className="banner error">Kırılım alınamadı: {error.message}</div> : null}
      {data && !data.available ? (
        <div className="empty">Reklam kaynağı verisi ilk saatlik güncellemeyle gelecek.</div>
      ) : !pivot ? (
        <div className="skeleton" />
      ) : (
        <div className="table-wrap">
          <table className="board ss-table">
            <thead>
              <tr>
                <th>Statü</th>
                {pivot.cols.map((c) => (
                  <th key={c} className="num" title={c}>
                    {c.length > 22 ? `${c.slice(0, 21)}…` : c}
                  </th>
                ))}
                <th className="num">Toplam</th>
              </tr>
            </thead>
            <tbody>
              {pivot.statuses.map((st) => {
                const total = pivot.rowTotal.get(st) ?? 0;
                return (
                  <tr key={st}>
                    <td className="name">{st}</td>
                    {pivot.cols.map((c) => {
                      const n = pivot.cells.get(st)?.get(c) ?? 0;
                      const share = total ? n / total : 0;
                      return (
                        <td
                          key={c}
                          className={`num ss-cell h${heat(share)}`}
                          title={`${st} · ${c}: ${int(n)} lead (statünün %${num(share * 100)}'i)`}
                        >
                          {n ? int(n) : <span className="muted">—</span>}
                          {n && share >= 0.1 ? <span className="ss-share">%{Math.round(share * 100)}</span> : null}
                        </td>
                      );
                    })}
                    <td className="num">{int(total)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td className="name">Toplam</td>
                {pivot.cols.map((c) => (
                  <td key={c} className="num">
                    {int(pivot.colTotal.get(c) ?? 0)}
                  </td>
                ))}
                <td className="num">{int(pivot.grand)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
      <p className="muted fn-note">
        Hücre rengi o statüdeki payı gösterir (koyu = statünün büyük kısmı bu kaynaktan). "Dönemde gelenler": seçili dönemde
        atanan lead'lerin bugünkü statüsü. Reklam alanları lead'lerin yaklaşık %60'ında dolu; gerisi "(boş)".
      </p>
    </>
  );
}
