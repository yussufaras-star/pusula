import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { MonthlyFlowChart } from "./charts";
import { COMPANY_SHORT, day, money, short, usd } from "./format";
import type { CompanyId, Flows, Meta, Tx } from "./types";
import { CompanyTag, FxNote, Loading, useFin, type TabProps } from "./ui";

type Kind = "all" | "in" | "out" | "transfer" | "uncategorized";
const KINDS: { key: Kind; label: string }[] = [
  { key: "all", label: "Tümü" },
  { key: "in", label: "Giriş" },
  { key: "out", label: "Çıkış" },
  { key: "transfer", label: "Transfer" },
  { key: "uncategorized", label: "Kategorisiz" },
];
const PAGE = 100;

export function FlowsTab({ scope, start, end, meta, tick }: TabProps) {
  const [kind, setKind] = useState<Kind>("all");
  const [category, setCategory] = useState<string>("");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [local, setLocal] = useState(0);

  // Arama kutusu yazarken her tuşta istek atmasın
  useEffect(() => {
    const t = window.setTimeout(() => setQ(query.trim()), 300);
    return () => window.clearTimeout(t);
  }, [query]);
  useEffect(() => setLimit(PAGE), [scope, start, end, kind, category, q]);

  const params = new URLSearchParams({ company: scope, start, end, kind, limit: String(limit) });
  if (category) params.set("category", category);
  if (q) params.set("q", q);
  const { data, error } = useFin<Flows>(`/api/finance/flows?${params}`, tick + local);
  if (!data) return <Loading error={error} />;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="tiles fin-tiles">
        <StatTile label="Giriş" value={short(data.totals.in)} expected="transferler hariç" />
        <StatTile label="Çıkış" value={short(data.totals.out)} expected="transferler hariç" />
        <StatTile label="Net" value={short(data.totals.net)} tone={data.totals.net >= 0 ? "good" : "critical"} badge={data.totals.net >= 0 ? "pozitif" : "negatif"} expected={`${data.months.length} ay`} />
        <StatTile label="Hareket" value={data.total} expected="seçili filtreyle" />
      </div>

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Aylık giriş ve çıkış</h2>
            <span className="hint">USD · işlem tarihindeki kurla · şirketler arası hariç</span>
          </div>
          <div className="card-body">
            <MonthlyFlowChart months={data.months} />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Kategori kırılımı</h2>
            <span className="hint">tıklayınca listeyi süzer</span>
          </div>
          <div className="card-body">
            {data.categories.length ? (
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Kategori</th>
                    <th className="num">Giriş</th>
                    <th className="num">Çıkış</th>
                  </tr>
                </thead>
                <tbody>
                  {data.categories.map((c) => {
                    const key = c.id ?? "_none";
                    return (
                      <tr key={key} className={category === key ? "picked" : undefined} onClick={() => setCategory(category === key ? "" : key)} style={{ cursor: "pointer" }}>
                        <td>
                          {c.name} <span className="muted">· {c.count}</span>
                        </td>
                        <td className="num">{c.in ? short(c.in) : "—"}</td>
                        <td className="num">{c.out ? short(c.out) : "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="empty">Veri yetersiz.</div>
            )}
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Hareketler</h2>
          <div className="controls">
            <div className="chips" role="group" aria-label="Tür">
              {KINDS.map((k) => (
                <button key={k.key} className={`chip${kind === k.key ? " on" : ""}`} aria-pressed={kind === k.key} onClick={() => setKind(k.key)}>
                  {k.label}
                </button>
              ))}
            </div>
            {category ? (
              <button className="chip on" onClick={() => setCategory("")} title="Kategori süzgecini kaldır">
                {category === "_none" ? "Kategorisiz" : (meta.categories.find((c) => c.id === category)?.name ?? category)} ×
              </button>
            ) : null}
            <input className="f-input" type="search" placeholder="Açıklama veya karşı taraf" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Ara" />
          </div>
        </div>
        <div className="card-body">
          <TxTable rows={data.transactions} meta={meta} editable onChanged={() => setLocal((n) => n + 1)} />
          {data.total > data.transactions.length ? (
            <div className="fin-more">
              <button className="btn" onClick={() => setLimit(limit + PAGE)}>
                Daha fazla ({data.total - data.transactions.length})
              </button>
            </div>
          ) : null}
        </div>
      </section>
    </>
  );
}

/** Hareket tablosu. editable: kategori ve şirketler arası işareti düzeltilebilir. */
export function TxTable({ rows, meta, editable, onChanged }: { rows: Tx[]; meta: Meta; editable?: boolean; onChanged?: () => void }) {
  if (!rows.length) return <div className="empty">Hareket yok.</div>;
  return (
    <div className="table-wrap">
      <table className="board fin-table">
        <thead>
          <tr>
            <th>Tarih</th>
            <th>Şirket</th>
            <th>Hesap</th>
            <th>Açıklama</th>
            <th className="num">Tutar</th>
            <th className="num">USD</th>
            <th>Kategori</th>
            <th>Şirketler arası</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <TxRow key={t.id} t={t} meta={meta} editable={editable} onChanged={onChanged} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TxRow({ t, meta, editable, onChanged }: { t: Tx; meta: Meta; editable?: boolean; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const others = meta.companies.filter((c) => c.id !== t.company_id);

  async function save(body: Record<string, unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/finance/transactions/${t.id}`, { method: "PATCH", body: JSON.stringify(body) });
      onChanged?.();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr style={{ opacity: busy ? 0.5 : 1 }}>
      <td>{day(t.booked_at)}</td>
      <td>
        <CompanyTag id={t.company_id} />
      </td>
      <td className="muted">{t.account}</td>
      <td className="fin-desc" title={t.counterparty ? `${t.description} · ${t.counterparty}` : t.description}>
        {t.description || <span className="muted">—</span>}
        {t.counterparty && t.counterparty !== t.description ? <div className="sub">{t.counterparty}</div> : null}
        {err ? <div className="warn-text">{err}</div> : null}
      </td>
      <td className={`num${t.amount > 0 ? " fin-pos" : ""}`}>{money(t.amount, t.currency, 2)}</td>
      <td className="num muted">{usd(t.amount_usd)}</td>
      <td>
        {editable ? (
          <select className="f-input sm" value={t.category_id ?? ""} disabled={busy} onChange={(e) => save({ category_id: e.target.value || null })} aria-label="Kategori">
            <option value="">Kategorisiz</option>
            {meta.categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        ) : (
          (meta.categories.find((c) => c.id === t.category_id)?.name ?? "Kategorisiz")
        )}
        {t.edited ? <span className="muted fin-edited" title="Elle düzeltildi; senkron değiştirmez"> · elle</span> : null}
      </td>
      <td>
        {editable ? (
          <select
            className="f-input sm"
            value={t.counterparty_company_id ?? ""}
            disabled={busy}
            onChange={(e) => save({ counterparty_company_id: (e.target.value || null) as CompanyId | null })}
            aria-label="Karşı şirket"
          >
            <option value="">—</option>
            {others.map((c) => (
              <option key={c.id} value={c.id}>
                {COMPANY_SHORT[c.id]}
              </option>
            ))}
          </select>
        ) : t.counterparty_company_id ? (
          <CompanyTag id={t.counterparty_company_id} />
        ) : (
          "—"
        )}
      </td>
    </tr>
  );
}
