import { useState } from "react";

import { api, ApiError } from "../api";
import { COMPANY_SHORT, usd } from "./format";
import type { Category, CategoryRule, Meta, Scope, Uncategorized, UncategorizedRow } from "./types";
import { CompanyTag, Field, Modal, useFin } from "./ui";

const DIRECTION: Record<"in" | "out" | "any", string> = { in: "giriş", out: "çıkış", any: "her iki yön" };

/** Yönle uyumlu kategoriler: giriş → gelir + transfer, çıkış → gider + transfer. */
function categoriesFor(meta: Meta, direction: "in" | "out" | "any"): Category[] {
  return meta.categories.filter(
    (c) => c.id !== "ic_transfer" && (direction === "any" || c.kind === direction || c.kind === "transfer"),
  );
}

/** En yüksek hacimli kategorisiz karşı taraflar: tek seçimle kural + toplu kategori. */
export function UncategorizedCard({ scope, meta, tick, onChanged }: { scope: Scope; meta: Meta; tick: number; onChanged: (note: string) => void }) {
  const { data, error } = useFin<Uncategorized>(`/api/finance/uncategorized?company=${scope}&limit=15`, tick);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function assign(row: UncategorizedRow, category: string) {
    const key = `${row.company_id}|${row.direction}|${row.pattern}`;
    setBusy(key);
    setErr(null);
    try {
      const r = await api<{ applied: number }>("/api/finance/rules", {
        method: "POST",
        body: JSON.stringify({
          pattern: row.pattern,
          label: row.label,
          match_type: "exact",
          direction: row.direction,
          company_id: row.company_id,
          category_id: category,
        }),
      });
      onChanged(`${row.label}: ${r.applied} hareket kategorilendi, sonraki hareketler de otomatik.`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Kategorisiz karşı taraflar</h2>
        <span className="hint">
          {data ? `${data.total} kategorisiz hareket · ` : ""}hacme göre · seçim kural olur, sonrakiler otomatik
        </span>
      </div>
      <div className="card-body">
        {err ? <div className="banner error">{err}</div> : null}
        {error ? <div className="banner error">{error.message}</div> : null}
        {!data ? (
          <div className="empty">Yükleniyor…</div>
        ) : data.items.length ? (
          <div className="table-wrap">
            <table className="board fin-table">
              <thead>
                <tr>
                  <th>Karşı taraf</th>
                  <th>Şirket</th>
                  <th>Yön</th>
                  <th className="num">Hareket</th>
                  <th className="num">USD</th>
                  <th>Kategori</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => {
                  const key = `${row.company_id}|${row.direction}|${row.pattern}`;
                  return (
                    <tr key={key} style={{ opacity: busy === key ? 0.5 : 1 }}>
                      <td className="fin-desc">{row.label}</td>
                      <td>
                        <CompanyTag id={row.company_id} />
                      </td>
                      <td>{DIRECTION[row.direction]}</td>
                      <td className="num">{row.count}</td>
                      <td className="num">{usd(row.usd)}</td>
                      <td>
                        <select
                          className="f-input sm"
                          value=""
                          disabled={busy !== null}
                          onChange={(e) => e.target.value && assign(row, e.target.value)}
                          aria-label={`${row.label} kategorisi`}
                        >
                          <option value="">Seç…</option>
                          {categoriesFor(meta, row.direction).map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.name}
                            </option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">Kategorisiz hareket yok.</div>
        )}
      </div>
    </section>
  );
}

/** Kural listesi: sistem ve elle kurallar, kaç hareketi kategorilediği; silme ve ekleme. */
export function RulesCard({ meta, tick, onChanged }: { meta: Meta; tick: number; onChanged: (note: string) => void }) {
  const { data } = useFin<{ rules: CategoryRule[] }>("/api/finance/rules", tick);
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [showSystem, setShowSystem] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const name = (id: string) => meta.categories.find((c) => c.id === id)?.name ?? id;
  const rules = (data?.rules ?? []).filter((r) => showSystem || r.source === "user");
  const userCount = (data?.rules ?? []).filter((r) => r.source === "user").length;
  const matched = (data?.rules ?? []).reduce((s, r) => s + r.matched, 0);

  async function remove(rule: CategoryRule) {
    if (!window.confirm(`"${rule.label ?? rule.pattern}" kuralı silinsin mi? Bu kuralın verdiği kategoriler geri alınır.`)) return;
    setErr(null);
    try {
      const r = await api<{ reverted: number; reapplied: number }>(`/api/finance/rules/${rule.id}`, { method: "DELETE" });
      onChanged(`Kural silindi: ${r.reverted} hareketin kategorisi geri alındı${r.reapplied ? `, ${r.reapplied} tanesi başka kuralla yeniden kategorilendi` : ""}.`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Silinemedi");
    }
  }

  return (
    <section className="card">
      <div className={`card-head${open ? "" : " solo"}`}>
        <h2>Kategori kuralları</h2>
        <div className="controls">
          <span className="hint">
            {userCount} elle kural · {matched} hareket kuralla kategorilendi
          </span>
          <button className="btn sm" onClick={() => setOpen(!open)}>
            {open ? "Gizle" : "Göster"}
          </button>
          <button className="btn sm" onClick={() => setAdding(true)}>
            Kural ekle
          </button>
        </div>
      </div>
      {open ? (
        <div className="card-body">
          {err ? <div className="banner error">{err}</div> : null}
          <label className="check">
            <input type="checkbox" checked={showSystem} onChange={(e) => setShowSystem(e.target.checked)} />
            Sistem kurallarını da göster (Stripe, IRS, DHL vb.)
          </label>
          {rules.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Kalıp</th>
                    <th>Eşleşme</th>
                    <th>Yön</th>
                    <th>Şirket</th>
                    <th>Kategori</th>
                    <th className="num">Hareket</th>
                    <th>Kaynak</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r) => (
                    <tr key={r.id}>
                      <td className="fin-desc">{r.label ?? r.pattern}</td>
                      <td>{r.match_type === "exact" ? "birebir" : "içerir"}</td>
                      <td>{DIRECTION[r.direction]}</td>
                      <td>{r.company_id ? COMPANY_SHORT[r.company_id] : "tümü"}</td>
                      <td>{name(r.category_id)}</td>
                      <td className="num">{r.matched}</td>
                      <td className="muted">{r.source === "user" ? "elle" : "sistem"}</td>
                      <td>
                        <button className="btn sm link-x" onClick={() => remove(r)} aria-label="Kuralı sil" title="Sil">
                          ×
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Henüz elle kural yok. Bir harekete veya karşı tarafa kategori seçince kural oluşur.</div>
          )}
        </div>
      ) : null}
      {adding ? (
        <RuleForm
          meta={meta}
          onClose={() => setAdding(false)}
          onSaved={(note) => {
            setAdding(false);
            onChanged(note);
          }}
        />
      ) : null}
    </section>
  );
}

function RuleForm({ meta, onClose, onSaved }: { meta: Meta; onClose: () => void; onSaved: (note: string) => void }) {
  const [pattern, setPattern] = useState("");
  const [direction, setDirection] = useState<"in" | "out" | "any">("out");
  const [match, setMatch] = useState<"contains" | "exact">("contains");
  const [company, setCompany] = useState("");
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cats = categoriesFor(meta, direction);

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ applied: number }>("/api/finance/rules", {
        method: "POST",
        body: JSON.stringify({ pattern, match_type: match, direction, company_id: company || null, category_id: category }),
      });
      onSaved(`Kural eklendi: ${r.applied} hareket kategorilendi.`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title="Kategori kuralı ekle" onClose={onClose}>
      <div className="f-grid">
        <Field label="Kalıp" hint="açıklama veya karşı tarafta aranır; büyük/küçük harf ve Türkçe harf farkı yok">
          <input className="f-input" value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="ör. print, aspol, google ads" />
        </Field>
        <Field label="Eşleşme">
          <select className="f-input" value={match} onChange={(e) => setMatch(e.target.value as "contains" | "exact")}>
            <option value="contains">İçerir (kelime başı)</option>
            <option value="exact">Karşı taraf birebir</option>
          </select>
        </Field>
        <Field label="Yön">
          <select
            className="f-input"
            value={direction}
            onChange={(e) => {
              setDirection(e.target.value as "in" | "out" | "any");
              setCategory("");
            }}
          >
            <option value="out">Çıkış</option>
            <option value="in">Giriş</option>
            <option value="any">Her iki yön</option>
          </select>
        </Field>
        <Field label="Şirket">
          <select className="f-input" value={company} onChange={(e) => setCompany(e.target.value)}>
            <option value="">Tüm şirketler</option>
            {meta.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Kategori">
          <select className="f-input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Seç…</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <p className="muted fin-note">Kural yalnız kategorisiz hareketlere uygulanır; elle düzeltilen hareketlere dokunmaz. Elle kural sistem kuralından önce gelir.</p>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        <div className="spacer" />
        <button className="btn" onClick={onClose}>
          Vazgeç
        </button>
        <button className="btn primary" onClick={save} disabled={busy || pattern.trim().length < 2 || !category}>
          {busy ? "Kaydediliyor…" : "Ekle"}
        </button>
      </div>
    </Modal>
  );
}
