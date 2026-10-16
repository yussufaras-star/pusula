import { useState } from "react";

import { api, ApiError } from "../api";
import { day, fileToBase64, money, usd, when } from "./format";
import type { Account, AccountsPayload, AccountType, CompanyId, Meta, Provider, StatementMapping, StatementPreview } from "./types";
import { CompanyTag, Field, FxNote, Loading, Modal, scopeIds, useFin, type TabProps } from "./ui";

const PROVIDER_LABEL: Record<Provider, string> = {
  mercury: "Mercury API",
  wise: "Wise API",
  stripe: "Stripe API",
  revolut: "Revolut Business API",
  csv: "Ekstre yükleme",
  manual: "Elle bakiye",
};
const TYPE_LABEL: Record<AccountType, string> = {
  bank: "Banka",
  fintech: "Fintech",
  payment: "Ödeme altyapısı",
  card: "Kredi kartı",
  shipping: "Kargo bakiyesi",
  other: "Diğer",
};
const API: Provider[] = ["mercury", "wise", "stripe", "revolut"];
const ID_HINT: Partial<Record<Provider, string>> = {
  mercury: "Mercury hesap id",
  wise: "<profileId>:<balanceId>",
  stripe: "serbest, ör. default",
  revolut: "Revolut hesap id",
};

export function AccountsTab({ scope, meta, tick, reload }: TabProps) {
  const [editing, setEditing] = useState<Account | "new" | null>(null);
  const [upload, setUpload] = useState<Account | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [local, setLocal] = useState(0);
  const { data, error } = useFin<AccountsPayload>(`/api/finance/accounts?company=${scope}`, tick + local);
  if (!data) return <Loading error={error} />;
  const rows = data.accounts.filter((a) => showInactive || a.active);
  const done = () => {
    setLocal((n) => n + 1);
    reload();
  };

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <section className="card">
        <div className="card-head">
          <h2>Hesaplar</h2>
          <div className="controls">
            <label className="check">
              <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
              Pasifleri göster
            </label>
            <button className="btn primary" onClick={() => setEditing("new")}>
              Hesap ekle
            </button>
          </div>
        </div>
        <div className="card-body">
          {rows.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Şirket</th>
                    <th>Hesap</th>
                    <th>Bağlantı</th>
                    <th className="num">Bakiye</th>
                    <th className="num">USD</th>
                    <th>Son güncelleme</th>
                    <th>Durum</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a) => (
                    <tr key={a.id} style={{ opacity: a.active ? 1 : 0.5 }}>
                      <td>
                        <CompanyTag id={a.company_id} />
                      </td>
                      <td className="fin-desc">
                        <b>{a.name}</b>
                        <div className="sub">
                          {a.institution} · {TYPE_LABEL[a.type]} · {a.currency}
                        </div>
                      </td>
                      <td>
                        {PROVIDER_LABEL[a.provider]}
                        {a.credential_key ? <div className="sub">anahtar eki {a.credential_key}</div> : null}
                      </td>
                      <td className="num">{money(a.balance, a.currency, 2)}</td>
                      <td className="num muted">{usd(a.balance_usd)}</td>
                      <td>{when(a.last_synced_at ?? a.balance_at)}</td>
                      <td className="fin-desc">
                        {a.sync_error ? (
                          <span className="warn-text">{a.sync_error}</span>
                        ) : a.sync_warning ? (
                          <span className="muted">{a.sync_warning}</span>
                        ) : a.last_synced_at ? (
                          <span className="badge good">güncel</span>
                        ) : (
                          <span className="muted">henüz senkron yok</span>
                        )}
                      </td>
                      <td className="fin-actions">
                        {a.provider === "csv" || a.provider === "manual" ? (
                          <button className="btn sm" onClick={() => setUpload(a)}>
                            Ekstre yükle
                          </button>
                        ) : null}
                        <button className="btn sm" onClick={() => setEditing(a)}>
                          Düzenle
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Henüz hesap yok.</div>
          )}
          <p className="muted fin-note">
            API anahtarları tarayıcıya ve veritabanına gelmez; Vercel ve GitHub ortam değişkenlerinde durur (MERCURY_API_TOKEN, WISE_API_TOKEN, STRIPE_RESTRICTED_KEY). Burada yalnız
            değişken adının son eki yazılır. Garanti, KuveytTürk, Vakıf gibi bankalar için bağlantı türü "Ekstre yükleme" seçilir.
          </p>
        </div>
      </section>

      {editing ? (
        <AccountForm
          account={editing === "new" ? null : editing}
          meta={meta}
          defaultCompany={scopeIds(scope, meta)[0]}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            done();
          }}
        />
      ) : null}
      {upload ? <StatementUpload account={upload} onClose={() => setUpload(null)} onImported={done} /> : null}
    </>
  );
}

function AccountForm({
  account,
  meta,
  defaultCompany,
  onClose,
  onSaved,
}: {
  account: Account | null;
  meta: Meta;
  defaultCompany: CompanyId;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState({
    company_id: account?.company_id ?? defaultCompany,
    name: account?.name ?? "",
    institution: account?.institution ?? "",
    type: account?.type ?? "bank",
    currency: account?.currency ?? meta.companies.find((c) => c.id === defaultCompany)?.base_currency ?? "USD",
    provider: account?.provider ?? "csv",
    provider_account_id: account?.provider_account_id ?? "",
    credential_key: account?.credential_key ?? "",
    current_balance: account ? String(account.balance) : "0",
    active: account?.active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f, v: string | boolean) => setF({ ...f, [k]: v });
  const isApi = API.includes(f.provider as Provider);

  async function save() {
    if (!f.name.trim() || !f.institution.trim()) {
      setErr("Hesap adı ve kurum gerekli.");
      return;
    }
    if (isApi && (!f.provider_account_id.trim() || !f.credential_key.trim())) {
      setErr("API hesabı için sağlayıcı hesap kimliği ve anahtar eki gerekli.");
      return;
    }
    const body: Record<string, unknown> = {
      company_id: f.company_id,
      name: f.name.trim(),
      institution: f.institution.trim(),
      type: f.type,
      currency: f.currency,
      provider: f.provider,
      provider_account_id: f.provider_account_id.trim() || null,
      credential_key: f.credential_key.trim().toUpperCase() || null,
      active: f.active,
    };
    // API hesabının bakiyesi senkronla gelir; elle yazılmaz
    if (!isApi && (!account || String(account.balance) !== f.current_balance)) body.current_balance = f.current_balance || "0";
    setBusy(true);
    setErr(null);
    try {
      await api(account ? `/api/finance/accounts/${account.id}` : "/api/finance/accounts", {
        method: account ? "PATCH" : "POST",
        body: JSON.stringify(body),
      });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title={account ? "Hesabı düzenle" : "Hesap ekle"} onClose={onClose} wide>
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
        <Field label="Hesap adı">
          <input className="f-input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="ör. Garanti TL vadesiz" />
        </Field>
        <Field label="Kurum">
          <input className="f-input" value={f.institution} onChange={(e) => set("institution", e.target.value)} placeholder="ör. Garanti BBVA" />
        </Field>
        <Field label="Tür">
          <select className="f-input" value={f.type} onChange={(e) => set("type", e.target.value)}>
            {(Object.keys(TYPE_LABEL) as AccountType[]).map((t) => (
              <option key={t} value={t}>
                {TYPE_LABEL[t]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Para birimi">
          <select className="f-input" value={f.currency} onChange={(e) => set("currency", e.target.value)}>
            {["USD", "EUR", "GBP", "TRY"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Bağlantı türü">
          <select className="f-input" value={f.provider} onChange={(e) => set("provider", e.target.value)}>
            {(Object.keys(PROVIDER_LABEL) as Provider[]).map((p) => (
              <option key={p} value={p}>
                {PROVIDER_LABEL[p]}
              </option>
            ))}
          </select>
        </Field>
        {isApi ? (
          <>
            <Field label="Sağlayıcı hesap kimliği" hint={ID_HINT[f.provider as Provider]}>
              <input className="f-input" value={f.provider_account_id} onChange={(e) => set("provider_account_id", e.target.value)} />
            </Field>
            <Field
              label="Anahtar eki"
              hint={
                f.provider === "revolut"
                  ? `ortam değişkeni: REVOLUT_*_${f.credential_key.toUpperCase() || "<EK>"}`
                  : `ortak: ${f.provider === "stripe" ? "STRIPE_RESTRICTED_KEY" : `${f.provider.toUpperCase()}_API_TOKEN`} · şirkete özel varsa ${f.provider === "stripe" ? "STRIPE_KEY" : `${f.provider.toUpperCase()}_TOKEN`}_${f.credential_key.toUpperCase() || "<EK>"}`
              }
            >
              <input className="f-input" value={f.credential_key} onChange={(e) => set("credential_key", e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))} placeholder="ör. LLC" />
            </Field>
          </>
        ) : (
          <Field label="Güncel bakiye" hint={f.provider === "csv" ? "ekstre yüklenince bakiye sütunundan güncellenir" : undefined}>
            <input className="f-input num" type="number" step="0.01" value={f.current_balance} onChange={(e) => set("current_balance", e.target.value)} />
          </Field>
        )}
      </div>
      <label className="check">
        <input type="checkbox" checked={f.active} onChange={(e) => set("active", e.target.checked)} />
        Aktif (pasif hesap nakde ve senkrona girmez; hareketleri silinmez)
      </label>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
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

// --- ekstre yükleme ---------------------------------------------------------------

type MapKey = keyof StatementMapping;

function StatementUpload({ account, onClose, onImported }: { account: Account; onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<{ name: string; b64: string } | null>(null);
  const [preview, setPreview] = useState<StatementPreview | null>(null);
  const [mapping, setMapping] = useState<StatementMapping | null>(null);
  const [split, setSplit] = useState(false);
  const [invert, setInvert] = useState(false);
  const [updateBalance, setUpdateBalance] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  async function runPreview(next: { name: string; b64: string }, m: StatementMapping | null, inv: boolean) {
    setBusy(true);
    setErr(null);
    try {
      const p = await api<StatementPreview>(`/api/finance/accounts/${account.id}/statement/preview`, {
        method: "POST",
        body: JSON.stringify({ filename: next.name, content_b64: next.b64, mapping: m, invert: inv }),
      });
      setPreview(p);
      if (p.mapping) {
        setMapping(p.mapping);
        setSplit(p.mapping.amount === null);
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Dosya okunamadı");
    } finally {
      setBusy(false);
    }
  }

  async function onFile(f: File) {
    setDone(null);
    setPreview(null);
    setMapping(null);
    const next = { name: f.name, b64: await fileToBase64(f) };
    setFile(next);
    runPreview(next, null, invert);
  }

  function change(key: MapKey, value: string) {
    if (!file) return;
    const base: StatementMapping = mapping ?? { date: 0, description: null, amount: null, debit: null, credit: null, balance: null };
    const next = { ...base, [key]: value === "" ? null : Number(value) };
    setMapping(next);
    if (next.amount !== null || next.debit !== null || next.credit !== null) runPreview(file, next, invert);
  }

  function setMode(isSplit: boolean) {
    if (!file || !mapping) return;
    setSplit(isSplit);
    const next = isSplit ? { ...mapping, amount: null } : { ...mapping, debit: null, credit: null, amount: mapping.amount ?? mapping.credit ?? mapping.debit ?? 0 };
    setMapping(next);
    if (!isSplit || next.debit !== null || next.credit !== null) runPreview(file, next, invert);
  }

  async function run() {
    if (!file || !mapping) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await api<{ inserted: number; duplicates: number; skipped: number; balance: number | null }>(
        `/api/finance/accounts/${account.id}/statement/import`,
        {
          method: "POST",
          body: JSON.stringify({ filename: file.name, content_b64: file.b64, mapping, invert, update_balance: updateBalance }),
        },
      );
      setDone(
        `${r.inserted} yeni hareket eklendi, ${r.duplicates} tanesi zaten vardı.` +
          (r.balance !== null ? ` Bakiye ${money(r.balance, account.currency, 2)} olarak güncellendi.` : ""),
      );
      onImported();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "İçe aktarılamadı");
    } finally {
      setBusy(false);
    }
  }

  const col = (key: MapKey, label: string, optional: boolean) =>
    preview ? (
      <Field label={label}>
        <select className="f-input" value={mapping?.[key] ?? ""} onChange={(e) => change(key, e.target.value)} disabled={busy}>
          {optional || mapping?.[key] === null || mapping?.[key] === undefined ? <option value="">— yok —</option> : null}
          {preview.header.map((h, i) => (
            <option key={i} value={i}>
              {h}
            </option>
          ))}
        </select>
      </Field>
    ) : null;

  const fresh = preview ? preview.count - preview.duplicates : 0;

  return (
    <Modal title={`Ekstre yükle: ${account.name}`} onClose={onClose} wide>
      <p className="muted fin-note">
        İnternet şubesinden hesap hareketlerini Excel (XLSX) veya CSV olarak indirip seç. Aynı dosyayı tekrar yüklemek çift kayıt oluşturmaz.
      </p>
      <input type="file" accept=".csv,.txt,.xls,.xlsx" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      {err ? <div className="banner error">{err}</div> : null}

      {preview ? (
        <>
          {!preview.mapping && !mapping ? <div className="banner">Tarih sütunu bulunamadı; sütunları seç.</div> : null}
          <div className="f-grid">
            {col("date", "Tarih sütunu", false)}
            {col("description", "Açıklama sütunu", true)}
            <Field label="Tutar biçimi">
              <select className="f-input" value={split ? "split" : "single"} onChange={(e) => setMode(e.target.value === "split")} disabled={busy || !mapping}>
                <option value="single">Tek işaretli tutar sütunu</option>
                <option value="split">Ayrı borç / alacak sütunları</option>
              </select>
            </Field>
            {split ? (
              <>
                {col("debit", "Borç (çıkış)", true)}
                {col("credit", "Alacak (giriş)", true)}
              </>
            ) : (
              col("amount", "Tutar sütunu", false)
            )}
            {col("balance", "Bakiye sütunu", true)}
          </div>
          <div className="fin-checks">
            {!split ? (
              <label className="check">
                <input
                  type="checkbox"
                  checked={invert}
                  onChange={(e) => {
                    setInvert(e.target.checked);
                    if (file && mapping) runPreview(file, mapping, e.target.checked);
                  }}
                />
                İşaretleri ters çevir (çıkışlar pozitif görünüyorsa)
              </label>
            ) : null}
            {preview.last_balance !== null ? (
              <label className="check">
                <input type="checkbox" checked={updateBalance} onChange={(e) => setUpdateBalance(e.target.checked)} />
                Hesap bakiyesini ekstreden güncelle ({money(preview.last_balance, account.currency, 2)})
              </label>
            ) : null}
          </div>
          <div className="muted fin-note">
            {preview.count} hareket okundu
            {preview.date_from ? ` (${day(preview.date_from)} – ${day(preview.date_to)})` : ""}
            {preview.skipped ? `, ${preview.skipped} satır atlandı (tarih veya tutar yok)` : ""}
            {preview.duplicates ? `, ${preview.duplicates} tanesi zaten kayıtlı` : ""}. İlk satırlar:
          </div>
          {preview.sample.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Tarih</th>
                    <th>Açıklama</th>
                    <th className="num">Tutar</th>
                    <th className="num">Bakiye</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.sample.map((t, i) => (
                    <tr key={i}>
                      <td>{day(t.booked_at)}</td>
                      <td className="fin-desc">{t.description}</td>
                      <td className={`num${t.amount > 0 ? " fin-pos" : ""}`}>{money(t.amount, account.currency, 2)}</td>
                      <td className="num muted">{t.balance === null ? "—" : money(t.balance, account.currency, 2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}

      {done ? <div className="banner fin-ok">{done}</div> : null}
      <div className="f-actions">
        <div className="spacer" />
        <button className="btn" onClick={onClose}>
          {done ? "Kapat" : "Vazgeç"}
        </button>
        {!done ? (
          <button className="btn primary" disabled={busy || !preview || !mapping || !preview.count} onClick={run}>
            {busy ? "Okunuyor…" : fresh > 0 ? `${fresh} yeni hareketi içe aktar` : "İçe aktar"}
          </button>
        ) : null}
      </div>
    </Modal>
  );
}
