import { useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, short, signed } from "./format";
import type { CompanyId, Decision, DecisionStatus, Decisions, Meta, Priority } from "./types";
import { CompanyTag, Field, Loading, Modal, useFin, type TabProps } from "./ui";

export const STATUS_LABEL: Record<DecisionStatus, string> = {
  acik: "Açık",
  aksiyon: "Aksiyon",
  devam: "Devam ediyor",
  tamam: "Tamam",
  iptal: "İptal",
};
const PRIORITY_LABEL: Record<Priority, string> = { yuksek: "Yüksek", orta: "Orta", dusuk: "Düşük" };
const ACTIVE: DecisionStatus[] = ["acik", "aksiyon", "devam"];

export function DecisionsTab({ scope, meta, tick }: TabProps) {
  const [local, setLocal] = useState(0);
  const [editing, setEditing] = useState<Decision | "new" | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { data, error } = useFin<Decisions>(`/api/finance/decisions?company=${scope}`, tick + local);
  const refresh = () => setLocal((n) => n + 1);
  if (!data) return <Loading error={error} />;
  const rows = data.decisions.filter((d) => showClosed || ACTIVE.includes(d.status));

  async function setStatus(d: Decision, status: DecisionStatus) {
    setErr(null);
    try {
      await api(`/api/finance/decisions/${d.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    }
  }

  return (
    <>
      <div className="tiles fin-tiles">
        <StatTile label="Açık karar" value={data.stats.open} expected="açık, aksiyon, devam" />
        <StatTile label="Aksiyon bekleyen" value={data.stats.action} expected="sorumlusu harekete geçecek" />
        <StatTile label="Vadesi geçen" value={data.stats.overdue} tone={data.stats.overdue ? "warning" : "neutral"} badge={data.stats.overdue ? "takip" : undefined} expected="hedef tarihi geçmiş" />
        <StatTile label="Tahmini yıllık etki" value={short(data.stats.impact_usd)} expected="açık kararların toplamı" />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Kararlar</h2>
          <div className="controls">
            <label className="check">
              <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} />
              Kapananları göster
            </label>
            <button className="btn primary" onClick={() => setEditing("new")}>
              Karar ekle
            </button>
          </div>
        </div>
        <div className="card-body">
          {err ? <div className="banner error">{err}</div> : null}
          {rows.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Karar</th>
                    <th>Kapsam</th>
                    <th>Sorumlu</th>
                    <th>Öncelik</th>
                    <th>Hedef</th>
                    <th className="num">Yıllık etki</th>
                    <th>Durum</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((d) => (
                    <tr key={d.id} style={{ opacity: ACTIVE.includes(d.status) ? 1 : 0.55 }}>
                      <td className="fin-desc">
                        <b>{d.title}</b>
                        {d.description ? <div className="sub">{d.description}</div> : null}
                      </td>
                      <td>
                        <CompanyTag id={d.company_id} />
                      </td>
                      <td>{d.owner ?? <span className="muted">—</span>}</td>
                      <td>{PRIORITY_LABEL[d.priority]}</td>
                      <td>
                        {day(d.due_date)}
                        {d.overdue ? <span className="badge warning">geçti</span> : null}
                      </td>
                      <td className="num">{d.impact_usd === null ? "—" : signed(d.impact_usd)}</td>
                      <td>
                        <select className="f-input sm" value={d.status} onChange={(e) => setStatus(d, e.target.value as DecisionStatus)} aria-label="Durum">
                          {(Object.keys(STATUS_LABEL) as DecisionStatus[]).map((s) => (
                            <option key={s} value={s}>
                              {STATUS_LABEL[s]}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td>
                        <button className="btn sm" onClick={() => setEditing(d)}>
                          Düzenle
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Açık karar yok.</div>
          )}
        </div>
      </section>

      {editing ? (
        <DecisionForm
          decision={editing === "new" ? null : editing}
          meta={meta}
          defaultCompany={scope === "all" ? null : scope}
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

function DecisionForm({
  decision,
  meta,
  defaultCompany,
  onClose,
  onSaved,
}: {
  decision: Decision | null;
  meta: Meta;
  defaultCompany: CompanyId | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState({
    title: decision?.title ?? "",
    description: decision?.description ?? "",
    company_id: decision ? (decision.company_id ?? "") : (defaultCompany ?? ""),
    owner: decision?.owner ?? "",
    status: decision?.status ?? "acik",
    priority: decision?.priority ?? "orta",
    due_date: decision?.due_date ?? "",
    impact_usd: decision?.impact_usd === null || decision?.impact_usd === undefined ? "" : String(decision.impact_usd),
    meeting_date: decision?.meeting_date ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });

  async function save() {
    if (!f.title.trim()) {
      setErr("Başlık gerekli.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body = {
      title: f.title.trim(),
      description: f.description.trim(),
      company_id: f.company_id || null,
      owner: f.owner.trim() || null,
      status: f.status,
      priority: f.priority,
      due_date: f.due_date || null,
      impact_usd: f.impact_usd === "" ? null : f.impact_usd,
      meeting_date: f.meeting_date || null,
    };
    try {
      await api(decision ? `/api/finance/decisions/${decision.id}` : "/api/finance/decisions", {
        method: decision ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }

  async function remove() {
    if (!decision || !window.confirm("Karar silinsin mi? Kapatmak için durumu Tamam veya İptal yapabilirsin.")) return;
    setBusy(true);
    try {
      await api(`/api/finance/decisions/${decision.id}`, { method: "DELETE" });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Silinemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title={decision ? "Kararı düzenle" : "Karar ekle"} onClose={onClose} wide>
      <div className="f-grid">
        <Field label="Başlık">
          <input className="f-input" value={f.title} onChange={(e) => set("title", e.target.value)} />
        </Field>
        <Field label="Kapsam">
          <select className="f-input" value={f.company_id} onChange={(e) => set("company_id", e.target.value)}>
            <option value="">Grup geneli</option>
            {meta.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Sorumlu">
          <input className="f-input" value={f.owner} onChange={(e) => set("owner", e.target.value)} />
        </Field>
        <Field label="Durum">
          <select className="f-input" value={f.status} onChange={(e) => set("status", e.target.value)}>
            {(Object.keys(STATUS_LABEL) as DecisionStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Öncelik">
          <select className="f-input" value={f.priority} onChange={(e) => set("priority", e.target.value)}>
            {(Object.keys(PRIORITY_LABEL) as Priority[]).map((p) => (
              <option key={p} value={p}>
                {PRIORITY_LABEL[p]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Hedef tarih">
          <input className="f-input" type="date" value={f.due_date} onChange={(e) => set("due_date", e.target.value)} />
        </Field>
        <Field label="Tahmini yıllık nakit etkisi (USD)" hint="tasarruf veya gelir +, maliyet −">
          <input className="f-input num" type="number" step={1000} value={f.impact_usd} onChange={(e) => set("impact_usd", e.target.value)} />
        </Field>
        <Field label="Toplantı tarihi">
          <input className="f-input" type="date" value={f.meeting_date} onChange={(e) => set("meeting_date", e.target.value)} />
        </Field>
      </div>
      <Field label="Açıklama">
        <textarea className="f-input" rows={3} value={f.description} onChange={(e) => set("description", e.target.value)} />
      </Field>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        {decision ? (
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
