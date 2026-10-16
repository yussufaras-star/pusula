import { Fragment, useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { ProjectionChart } from "./charts";
import { day, istanbulToday, money, short, usd } from "./format";
import type { CompanyId, Meta, PlannedItem, Projection } from "./types";
import { CompanyTag, Field, FxNote, Loading, Modal, scopeIds, useFin, type TabProps } from "./ui";

const RECUR: Record<PlannedItem["recurrence"], string> = { none: "tek sefer", weekly: "haftalık", monthly: "aylık" };
const STATUS: Record<PlannedItem["status"], string> = { open: "açık", done: "tamamlandı", cancelled: "iptal" };

function readFloor(): number {
  try {
    return Number(window.localStorage.getItem("fin.floor")) || 0;
  } catch {
    return 0;
  }
}

export function ProjectionTab({ scope, meta, tick }: TabProps) {
  const [weeks, setWeeks] = useState<13 | 26>(13);
  const [weighted, setWeighted] = useState(true);
  const [floor, setFloor] = useState<number>(readFloor);
  const [open, setOpen] = useState<number | null>(null);
  const [editing, setEditing] = useState<PlannedItem | "new" | null>(null);
  const [local, setLocal] = useState(0);
  const path = `/api/finance/projection?company=${scope}&weeks=${weeks}&weighted=${weighted}&floor=${floor}`;
  const { data, error } = useFin<Projection>(path, tick + local);
  const refresh = () => setLocal((n) => n + 1);

  const saveFloor = (v: number) => {
    setFloor(v);
    try {
      window.localStorage.setItem("fin.floor", String(v));
    } catch {
      /* yok say */
    }
  };

  if (!data) return <Loading error={error} />;
  const breach = data.first_below;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="controls">
        <div className="seg small" role="tablist" aria-label="Ufuk">
          {([13, 26] as const).map((w) => (
            <button key={w} role="tab" aria-selected={weeks === w} onClick={() => setWeeks(w)}>
              {w} hafta
            </button>
          ))}
        </div>
        <div className="seg small" role="tablist" aria-label="Tutar">
          <button role="tab" aria-selected={weighted} onClick={() => setWeighted(true)} title="Tutar × olasılık">
            Olasılık ağırlıklı
          </button>
          <button role="tab" aria-selected={!weighted} onClick={() => setWeighted(false)}>
            Tam tutar
          </button>
        </div>
        <label className="field">
          Alt limit (USD)
          <input className="f-input num" type="number" step={5000} min={0} value={floor} onChange={(e) => saveFloor(Math.max(0, Number(e.target.value) || 0))} style={{ width: 120 }} />
        </label>
        <div className="spacer" />
        <button className="btn primary" onClick={() => setEditing("new")}>
          Planlı kalem ekle
        </button>
      </div>

      {breach ? (
        <div className={`banner${breach.close < 0 ? " error" : ""}`}>
          {day(breach.week)} haftasında kasa {usd(breach.close)} ile {breach.close < 0 ? "eksiye" : `alt limitin (${usd(floor)}) altına`} iniyor.
        </div>
      ) : null}

      <div className="tiles fin-tiles">
        <StatTile label="Bugünkü kasa" value={short(data.start_cash_usd)} expected="aktif hesaplar, son kur" />
        <StatTile
          label="En düşük kasa"
          value={data.lowest ? short(data.lowest.close) : "—"}
          expected={data.lowest ? `${day(data.lowest.week)} haftası` : "veri yetersiz"}
          tone={data.lowest && data.lowest.close < floor ? "critical" : "good"}
          badge={data.lowest ? (data.lowest.close < floor ? "limit altında" : "limit üstünde") : undefined}
        />
        <StatTile label={`${weeks} hafta sonu`} value={short(data.weeks.at(-1)?.close)} expected={weighted ? "olasılık ağırlıklı" : "tam tutar"} />
        <StatTile label="Açık kalem" value={data.items.filter((i) => i.status === "open").length} expected={`${data.items.filter((i) => i.overdue).length} vadesi geçmiş`} />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Haftalık kasa</h2>
          <span className="hint">USD · son kur · çubuğa tıkla, kalemler aşağıda açılır</span>
        </div>
        <div className="card-body">
          <ProjectionChart weeks={data.weeks} floor={floor} onPick={(i) => setOpen(open === i ? null : i)} />
          <div className="table-wrap">
            <table className="board fin-table">
              <thead>
                <tr>
                  <th>Hafta</th>
                  <th className="num">Giriş</th>
                  <th className="num">Çıkış</th>
                  <th className="num">Kapanış</th>
                </tr>
              </thead>
              <tbody>
                {data.weeks.map((w, i) => (
                  <Fragment key={w.week}>
                    <tr onClick={() => setOpen(open === i ? null : i)} style={{ cursor: w.items.length ? "pointer" : "default" }}>
                      <td>
                        {day(w.week)} {w.items.length ? <span className="muted">· {w.items.length} kalem {open === i ? "▴" : "▾"}</span> : null}
                      </td>
                      <td className="num">{w.in ? usd(w.in) : "—"}</td>
                      <td className="num">{w.out ? usd(w.out) : "—"}</td>
                      <td className={`num${w.close < floor ? " fin-neg" : ""}`}>
                        <b>{usd(w.close)}</b>
                      </td>
                    </tr>
                    {open === i
                      ? w.items.map((it, j) => (
                          <tr key={`${w.week}-${j}`} className="fin-sub">
                            <td colSpan={3}>
                              <CompanyTag id={it.company_id} /> {it.title} <span className="muted">· {day(it.day)}</span>
                              {it.overdue ? <span className="badge warning">gecikmiş</span> : null}
                            </td>
                            <td className={`num${it.usd > 0 ? " fin-pos" : ""}`}>{usd(it.usd)}</td>
                          </tr>
                        ))
                      : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Planlı kalemler</h2>
          <span className="hint">vadeli alacak ve borçlar, tekrarlayan ödemeler</span>
        </div>
        <div className="card-body">
          <PlannedTable items={data.items} onEdit={setEditing} onChanged={refresh} />
        </div>
      </section>

      {editing ? (
        <PlannedForm
          item={editing === "new" ? null : editing}
          meta={meta}
          defaultCompany={scopeIds(scope, meta)[0]}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}

function PlannedTable({ items, onEdit, onChanged }: { items: PlannedItem[]; onEdit: (p: PlannedItem) => void; onChanged: () => void }) {
  const [err, setErr] = useState<string | null>(null);
  if (!items.length) return <div className="empty">Planlı kalem yok.</div>;

  async function setStatus(p: PlannedItem, status: PlannedItem["status"]) {
    setErr(null);
    try {
      await api(`/api/finance/planned/${p.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    }
  }

  return (
    <div className="table-wrap">
      {err ? <div className="banner error">{err}</div> : null}
      <table className="board fin-table">
        <thead>
          <tr>
            <th>Vade</th>
            <th>Şirket</th>
            <th>Kalem</th>
            <th className="num">Tutar</th>
            <th className="num">USD</th>
            <th>Tekrar</th>
            <th className="num">Olasılık</th>
            <th>Durum</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id} style={{ opacity: p.status === "open" ? 1 : 0.55 }}>
              <td>
                {day(p.due_date)}
                {p.overdue ? <span className="badge warning">vadesi geçti</span> : null}
              </td>
              <td>
                <CompanyTag id={p.company_id} />
              </td>
              <td className="fin-desc">
                {p.title}
                {p.counterparty ? <div className="sub">{p.counterparty}</div> : null}
              </td>
              <td className={`num${p.direction === "in" ? " fin-pos" : ""}`}>
                {p.direction === "in" ? "+" : "−"}
                {money(p.amount, p.currency)}
              </td>
              <td className="num muted">{usd(p.amount_usd)}</td>
              <td>
                {RECUR[p.recurrence]}
                {p.recur_until ? <span className="muted"> · bitiş {day(p.recur_until)}</span> : null}
              </td>
              <td className="num">%{Math.round(p.probability * 100)}</td>
              <td>{STATUS[p.status]}</td>
              <td className="fin-actions">
                <button className="btn sm" onClick={() => onEdit(p)}>
                  Düzenle
                </button>
                {p.status === "open" ? (
                  <button className="btn sm" onClick={() => setStatus(p, "done")} title="Ödendi / tahsil edildi">
                    Kapat
                  </button>
                ) : (
                  <button className="btn sm" onClick={() => setStatus(p, "open")}>
                    Aç
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlannedForm({
  item,
  meta,
  defaultCompany,
  onClose,
  onSaved,
}: {
  item: PlannedItem | null;
  meta: Meta;
  defaultCompany: CompanyId;
  onClose: () => void;
  onSaved: () => void;
}) {
  const company = meta.companies.find((c) => c.id === (item?.company_id ?? defaultCompany));
  const [f, setF] = useState({
    company_id: item?.company_id ?? defaultCompany,
    direction: item?.direction ?? "out",
    title: item?.title ?? "",
    counterparty: item?.counterparty ?? "",
    amount: item ? String(item.amount) : "",
    currency: item?.currency ?? company?.base_currency ?? "USD",
    due_date: item?.due_date ?? istanbulToday(),
    recurrence: item?.recurrence ?? "none",
    recur_until: item?.recur_until ?? "",
    probability: item ? String(Math.round(item.probability * 100)) : "100",
    category_id: item?.category_id ?? "",
    notes: item?.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  const cats = meta.categories.filter((c) => c.kind === (f.direction === "in" ? "in" : "out"));

  async function save() {
    if (!f.title.trim() || !f.amount || !f.due_date) {
      setErr("Başlık, tutar ve vade gerekli.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body = {
      company_id: f.company_id,
      direction: f.direction,
      title: f.title.trim(),
      counterparty: f.counterparty.trim() || null,
      amount: f.amount,
      currency: f.currency,
      due_date: f.due_date,
      recurrence: f.recurrence,
      recur_until: f.recurrence !== "none" && f.recur_until ? f.recur_until : null,
      probability: (Number(f.probability) / 100).toFixed(2),
      category_id: f.category_id || null,
      notes: f.notes.trim() || null,
    };
    try {
      await api(item ? `/api/finance/planned/${item.id}` : "/api/finance/planned", {
        method: item ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!item || !window.confirm("Kalem silinsin mi? Tekrarlayan kalemi kapatmak için bitiş tarihi de verebilirsin.")) return;
    setBusy(true);
    try {
      await api(`/api/finance/planned/${item.id}`, { method: "DELETE" });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Silinemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title={item ? "Planlı kalemi düzenle" : "Planlı kalem ekle"} onClose={onClose}>
      <div className="f-grid">
        <Field label="Şirket">
          <select className="f-input" value={f.company_id} onChange={(e) => set("company_id", e.target.value)}>
            {meta.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Yön">
          <select className="f-input" value={f.direction} onChange={(e) => set("direction", e.target.value)}>
            <option value="in">Alacak (giriş)</option>
            <option value="out">Borç (çıkış)</option>
          </select>
        </Field>
        <Field label="Başlık">
          <input className="f-input" value={f.title} onChange={(e) => set("title", e.target.value)} />
        </Field>
        <Field label="Karşı taraf">
          <input className="f-input" value={f.counterparty} onChange={(e) => set("counterparty", e.target.value)} />
        </Field>
        <Field label="Tutar">
          <input className="f-input num" type="number" min={0} step="0.01" value={f.amount} onChange={(e) => set("amount", e.target.value)} />
        </Field>
        <Field label="Para birimi">
          <select className="f-input" value={f.currency} onChange={(e) => set("currency", e.target.value)}>
            {["USD", "EUR", "GBP", "TRY"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Vade">
          <input className="f-input" type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} />
        </Field>
        <Field label="Tekrar">
          <select className="f-input" value={f.recurrence} onChange={(e) => set("recurrence", e.target.value)}>
            <option value="none">Tek sefer</option>
            <option value="weekly">Haftalık</option>
            <option value="monthly">Aylık</option>
          </select>
        </Field>
        {f.recurrence !== "none" ? (
          <Field label="Bitiş" hint="boşsa süresiz">
            <input className="f-input" type="date" value={f.recur_until} min={f.due_date} onChange={(e) => set("recur_until", e.target.value)} />
          </Field>
        ) : null}
        <Field label="Olasılık (%)">
          <input className="f-input num" type="number" min={0} max={100} step={5} value={f.probability} onChange={(e) => set("probability", e.target.value)} />
        </Field>
        <Field label="Kategori">
          <select className="f-input" value={f.category_id} onChange={(e) => set("category_id", e.target.value)}>
            <option value="">—</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Not">
          <input className="f-input" value={f.notes} onChange={(e) => set("notes", e.target.value)} />
        </Field>
      </div>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        {item ? (
          <button className="btn danger" onClick={remove} disabled={busy}>
            Sil
          </button>
        ) : null}
        <div className="spacer" />
        <button className="btn" onClick={onClose}>
          Vazgeç
        </button>
        <button className="btn primary" onClick={save} disabled={busy}>
          {busy ? "Kaydediliyor…" : "Kaydet"}
        </button>
      </div>
    </Modal>
  );
}
