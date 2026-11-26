import { useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { COMPANY_SHORT, day, money, short, usd } from "./format";
import type { CompanyId, Meta, PayBasis, PayKind, PayrollLine, PayrollPayload, PayrollPerson } from "./types";
import { CompanyTag, Field, FxNote, Loading, Modal, useFin, type TabProps } from "./ui";

const KIND_LABEL: Record<PayKind, string> = { bordro: "Bordro", sozlesme: "Sözleşme" };
function tenure(start: string): string {
  const [y, m, d] = start.split("-").map(Number);
  const now = new Date();
  const months = (now.getFullYear() - y) * 12 + now.getMonth() + 1 - m - (now.getDate() < d ? 1 : 0);
  if (months < 1) return "<1 ay";
  const yy = Math.floor(months / 12);
  const mm = months % 12;
  return [yy ? `${yy} yıl` : "", mm ? `${mm} ay` : ""].filter(Boolean).join(" ");
}
const pct = (v: number) => `%${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(v * 100)}`;

export function PayrollTab({ scope, meta, tick }: TabProps) {
  const [local, setLocal] = useState(0);
  const [editing, setEditing] = useState<PayrollLine | Partial<PayrollLine> | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const { data, error } = useFin<PayrollPayload>(`/api/finance/payroll?company=${scope}`, tick + local);
  if (!data) return <Loading error={error} />;
  const t = data.totals;
  const tax = t.employee_usd + t.employer_usd;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="tiles fin-tiles">
        <StatTile label="Aylık maaş yükü" value={short(t.total_usd)} title={usd(t.total_usd)} expected={`${data.headcount} kişi · yıllık ${short(t.total_usd * 12)}`} />
        <StatTile
          label="Kişi başı yıllık maliyet"
          value={short((data.per_head_monthly_usd ?? 0) * 12)}
          title={usd((data.per_head_monthly_usd ?? 0) * 12)}
          expected={`C-Level hariç ${data.staff_headcount} kişi · aylık ${short(data.per_head_monthly_usd)}`}
        />
        <StatTile label="Çalışana net ödenen" value={short(t.net_usd)} title={usd(t.net_usd)} expected="aylık, sözleşmeler dahil" />
        <StatTile
          label="Yük çarpanı"
          value={data.burden_ratio ? `${data.burden_ratio.toLocaleString("tr-TR", { maximumFractionDigits: 2 })}×` : "—"}
          expected={`net 1 → maliyet; vergi ve sigorta aylık ${short(tax)}`}
        />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Maaş yükü bileşenleri</h2>
          <span className="muted">aylık, USD{data.fx_date ? ` · kur ${day(data.fx_date)}` : ""}</span>
        </div>
        <div className="card-body">
          <div className="table-wrap">
            <table className="board fin-table">
              <thead>
                <tr>
                  <th>Şirket</th>
                  <th className="num">Kişi</th>
                  <th className="num">Net ödenen</th>
                  <th className="num">Çalışan vergi + sigorta</th>
                  <th className="num">İşveren payı</th>
                  <th className="num">Yan hak</th>
                  <th className="num">Kıdem karşılığı</th>
                  <th className="num">Toplam maliyet</th>
                  <th className="num">Yıllık</th>
                </tr>
              </thead>
              <tbody>
                {data.companies.map((c) => (
                  <tr key={c.company_id}>
                    <td>
                      <CompanyTag id={c.company_id} />
                    </td>
                    <td className="num">{c.headcount}</td>
                    <td className="num">{usd(c.net_usd)}</td>
                    <td className="num">{usd(c.employee_usd)}</td>
                    <td className="num">{usd(c.employer_usd)}</td>
                    <td className="num">{usd(c.benefits_usd)}</td>
                    <td className="num">{usd(c.severance_usd)}</td>
                    <td className="num">
                      <b>{usd(c.total_usd)}</b>
                    </td>
                    <td className="num">{short(c.total_usd * 12)}</td>
                  </tr>
                ))}
                <tr className="pay-total">
                  <td>
                    <b>Toplam</b>
                  </td>
                  <td className="num">{data.headcount}</td>
                  <td className="num">{usd(t.net_usd)}</td>
                  <td className="num">{usd(t.employee_usd)}</td>
                  <td className="num">{usd(t.employer_usd)}</td>
                  <td className="num">{usd(t.benefits_usd)}</td>
                  <td className="num">{usd(t.severance_usd)}</td>
                  <td className="num">
                    <b>{usd(t.total_usd)}</b>
                  </td>
                  <td className="num">
                    <b>{short(t.total_usd * 12)}</b>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="muted fin-note">
            Net + çalışan kesintileri = brüt. Brüt + işveren payı + yan hak + kıdem karşılığı = işverene toplam maliyet. AŞ: 2026 gelir vergisi
            tarifesi ve asgari ücret istisnası, işveren SGK + işsizlik {pct(data.params.tr_sgk_isveren)} (2 puan teşvikli, imalat dışı), kıdem
            karşılığı brüt/12 ({money(data.params.tr_kidem_tavan, "TRY")} tavanla). GmbH: vergi sınıfı I, kilise vergisi yok; işveren sosyal
            sigorta payı + {pct(data.params.de_umlagen)} ek yük. Sözleşme (LLC, freelancer, Bağ-Kur): ödenen tutar. Kişi sayısında iki şirketten
            ödenen biri iki şirkette de sayılır; toplamda bir kez.
          </p>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Çalışanlar</h2>
          <button className="btn primary" onClick={() => setEditing({ company_id: scope === "all" ? "tr" : scope, kind: "bordro", currency: "TRY", basis: "net" })}>
            Ödeme ekle
          </button>
        </div>
        <div className="card-body">
          {data.people.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Kişi</th>
                    <th>Kıdem</th>
                    <th>Ödeme</th>
                    <th className="num">Net</th>
                    <th className="num">Brüt</th>
                    <th className="num">İşveren payı</th>
                    <th className="num">Aylık maliyet</th>
                    <th className="num">Yıllık</th>
                  </tr>
                </thead>
                <tbody>
                  {data.people.map((p) => (
                    <PersonRow key={p.person} p={p} open={open === p.person} onToggle={() => setOpen(open === p.person ? null : p.person)} onEdit={setEditing} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Kayıt yok. “Ödeme ekle” ile çalışan ve ödeme kanalını gir.</div>
          )}
          {data.inactive.length ? (
            <details className="fin-note">
              <summary className="muted">Pasif kayıtlar ({data.inactive.length})</summary>
              {data.inactive.map((l) => (
                <div key={l.id}>
                  {l.person} · {payLabel(l)}{" "}
                  <button className="btn sm" onClick={() => setEditing(l)}>
                    Düzenle
                  </button>
                </div>
              ))}
            </details>
          ) : null}
        </div>
      </section>

      {editing ? (
        <PayrollForm
          line={editing}
          meta={meta}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            setLocal((n) => n + 1);
          }}
        />
      ) : null}
    </>
  );
}

function payLabel(l: Pick<PayrollLine, "kind" | "basis" | "company_id" | "label" | "amount" | "currency">): string {
  const basis = l.kind === "bordro" ? ` ${l.basis === "net" ? "net" : "brüt"}` : "";
  return `${COMPANY_SHORT[l.company_id]} · ${l.label || KIND_LABEL[l.kind]} · ${money(l.amount, l.currency)}${basis}`;
}

function PersonRow({ p, open, onToggle, onEdit }: { p: PayrollPerson; open: boolean; onToggle: () => void; onEdit: (l: PayrollLine) => void }) {
  return (
    <>
      <tr>
        <td className="fin-desc">
          <button className="pay-name" onClick={onToggle} aria-expanded={open}>
            <b>{p.person}</b>
          </button>
          {p.c_level ? <span className="badge">C-Level</span> : null}
          <div className="sub">
            {p.unit || "—"}
            {p.start_date ? ` · ${day(p.start_date)}` : ""}
          </div>
        </td>
        <td style={{ whiteSpace: "nowrap" }}>{p.start_date ? tenure(p.start_date) : "—"}</td>
        <td>
          {p.lines.map((l) => (
            <div key={l.id}>{payLabel(l)}</div>
          ))}
        </td>
        <td className="num">{usd(p.net_usd)}</td>
        <td className="num">{usd(p.net_usd + p.employee_usd)}</td>
        <td className="num">{usd(p.employer_usd)}</td>
        <td className="num">
          <b>{usd(p.total_usd)}</b>
        </td>
        <td className="num">{short(p.total_usd * 12)}</td>
      </tr>
      {open
        ? p.lines.map((l) => (
            <tr key={l.id} className="pay-sub">
              <td className="fin-desc">
                <span className="muted">{l.notes || " "}</span>
              </td>
              <td />
              <td>
                <CompanyTag id={l.company_id} /> {l.label || KIND_LABEL[l.kind]}
              </td>
              <td className="num">{money(l.net, l.currency)}</td>
              <td className="num">{money(l.brut, l.currency)}</td>
              <td className="num">{money(l.employer, l.currency)}</td>
              <td className="num" title={`yan hak ${money(l.benefits, l.currency)} · kıdem ${money(l.severance, l.currency)}`}>
                {money(l.total, l.currency)}
              </td>
              <td className="num">
                <button className="btn sm" onClick={() => onEdit(l)}>
                  Düzenle
                </button>
              </td>
            </tr>
          ))
        : null}
    </>
  );
}

function PayrollForm({ line, meta, onClose, onSaved }: { line: Partial<PayrollLine>; meta: Meta; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    person: line.person ?? "",
    unit: line.unit ?? "",
    c_level: line.c_level ?? false,
    start_date: line.start_date ?? "",
    company_id: (line.company_id ?? "tr") as CompanyId,
    kind: (line.kind ?? "bordro") as PayKind,
    currency: line.currency ?? "TRY",
    amount: line.amount === undefined ? "" : String(line.amount),
    basis: (line.basis ?? "net") as PayBasis,
    meal: line.meal ? String(line.meal) : "",
    label: line.label ?? "",
    active: line.active ?? true,
    notes: line.notes ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF({ ...f, [k]: v });
  const bordroAllowed = f.company_id !== "llc";

  async function save() {
    if (!f.person.trim() || f.amount === "") {
      setErr("Ad ve tutar gerekli.");
      return;
    }
    setBusy(true);
    setErr(null);
    const body = {
      person: f.person.trim(),
      unit: f.unit.trim(),
      c_level: f.c_level,
      start_date: f.start_date || null,
      company_id: f.company_id,
      kind: bordroAllowed ? f.kind : "sozlesme",
      currency: f.currency,
      amount: f.amount,
      basis: f.basis,
      meal: f.meal || "0",
      label: f.label.trim() || null,
      active: f.active,
      notes: f.notes.trim() || null,
    };
    try {
      await api(line.id ? `/api/finance/payroll/${line.id}` : "/api/finance/payroll", {
        method: line.id ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }

  async function remove() {
    if (!line.id || !window.confirm("Bu ödeme satırı silinsin mi? Ayrılan kişi için “Aktif” işaretini kaldırmak geçmişi korur.")) return;
    setBusy(true);
    try {
      await api(`/api/finance/payroll/${line.id}`, { method: "DELETE" });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Silinemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title={line.id ? "Ödemeyi düzenle" : "Ödeme ekle"} onClose={onClose} wide>
      <div className="f-grid">
        <Field label="Ad soyad">
          <input className="f-input" value={f.person} onChange={(e) => set("person", e.target.value)} />
        </Field>
        <Field label="Birim">
          <input className="f-input" value={f.unit} onChange={(e) => set("unit", e.target.value)} />
        </Field>
        <Field label="İşe giriş">
          <input className="f-input" type="date" value={f.start_date} onChange={(e) => set("start_date", e.target.value)} />
        </Field>
        <Field label="Şirket">
          <select className="f-input" value={f.company_id} onChange={(e) => set("company_id", e.target.value as CompanyId)}>
            {meta.companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Tür" hint={bordroAllowed ? "Bordro: vergi ve sigorta hesaplanır" : "LLC ödemesi sözleşmedir"}>
          <select className="f-input" value={bordroAllowed ? f.kind : "sozlesme"} disabled={!bordroAllowed} onChange={(e) => set("kind", e.target.value as PayKind)}>
            <option value="bordro">Bordro</option>
            <option value="sozlesme">Sözleşme / fatura</option>
          </select>
        </Field>
        <Field label="Tutar (aylık)">
          <input className="f-input num" type="number" step={100} value={f.amount} onChange={(e) => set("amount", e.target.value)} />
        </Field>
        <Field label="Para birimi">
          <select className="f-input" value={f.currency} onChange={(e) => set("currency", e.target.value)}>
            {["TRY", "EUR", "USD"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        {bordroAllowed && f.kind === "bordro" ? (
          <Field label="Net / brüt">
            <select className="f-input" value={f.basis} onChange={(e) => set("basis", e.target.value as PayBasis)}>
              <option value="net">Net</option>
              <option value="brut">Brüt</option>
            </select>
          </Field>
        ) : null}
        <Field label="Yemek kartı (aylık)" hint="multinet vb.; maaşa dahil değilse">
          <input className="f-input num" type="number" step={100} value={f.meal} onChange={(e) => set("meal", e.target.value)} />
        </Field>
        <Field label="Etiket" hint="ör. Bağ-Kur, Freelancer">
          <input className="f-input" value={f.label} onChange={(e) => set("label", e.target.value)} />
        </Field>
      </div>
      <Field label="Not">
        <input className="f-input" value={f.notes} onChange={(e) => set("notes", e.target.value)} />
      </Field>
      <div className="controls">
        <label className="check">
          <input type="checkbox" checked={f.c_level} onChange={(e) => set("c_level", e.target.checked)} />
          C-Level
        </label>
        <label className="check">
          <input type="checkbox" checked={f.active} onChange={(e) => set("active", e.target.checked)} />
          Aktif
        </label>
      </div>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        {line.id ? (
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
