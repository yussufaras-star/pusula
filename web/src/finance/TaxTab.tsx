import { useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, istanbulToday, money, short, usd } from "./format";
import type { TaxPayload, TaxScenario, Wallet, WalletKind } from "./types";
import { Field, FxNote, Loading, Modal, useFin, type TabProps } from "./ui";

// Şimdilik yalnız Rexven LLC: GmbH ve TR sonra.
const COMPANY = "llc";
const CURRENCIES = ["USD", "EUR", "TRY", "GBP"];

const KIND_TITLE: Record<WalletKind, string> = {
  seller: "Satıcı cüzdanları",
  producer: "Üretici cüzdanları",
};
const KIND_HINT: Record<WalletKind, string> = {
  seller: "Satıcıların yüklediği, henüz harcanmamış para. Platformdaki toplamı gir; tek satır yeterli.",
  producer: "Üreticilere ödenecek tutar. Avans verdiysen eksi gir.",
};

/** 0.21 → %21 ; 0.065 → %6,5 */
export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `%${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(v * 100)}`;
}

export function TaxTab({ scope, start, end, tick }: TabProps) {
  const [local, setLocal] = useState(0);
  const [wallet, setWallet] = useState<Wallet | WalletKind | null>(null);
  const [scenario, setScenario] = useState<TaxScenario | "new" | null>(null);
  const { data, error } = useFin<TaxPayload>(`/api/finance/tax?company=${COMPANY}&start=${start}&end=${end}`, tick + local);
  const refresh = () => setLocal((n) => n + 1);
  if (!data) return <Loading error={error} />;

  const b = data.base;
  const sellers = data.wallets.filter((w) => w.kind === "seller");
  const noSeller = !sellers.some((w) => w.active);
  const dep = data.deposits;

  return (
    <>
      {scope !== "all" && scope !== COMPANY ? (
        <div className="banner">Bu ekran şimdilik yalnız Rexven LLC için. GmbH ve TR sonra eklenecek.</div>
      ) : null}
      <FxNote missing={data.missing_fx} />
      {noSeller ? (
        <div className="banner">
          Satıcı cüzdan bakiyesi girilmedi: kalan net varlık ve vergi olduğundan yüksek görünür. Aşağıdan satıcıların toplam
          cüzdan bakiyesini ekle.
        </div>
      ) : null}

      <div className="tiles fin-tiles">
        <StatTile label="Hesaplardaki varlık" value={short(b.cash_usd)} expected={`${data.accounts.length} LLC hesabı, güncel bakiye`} title={usd(b.cash_usd)} />
        <StatTile label="− Satıcı cüzdanları" value={short(b.seller_usd)} expected="satıcıya borç, elle girilir" title={usd(b.seller_usd)} />
        <StatTile label="− Üretici bakiyeleri" value={short(b.producer_usd)} expected="üreticiye borç, elle girilir" title={usd(b.producer_usd)} />
        <StatTile
          label="= Kalan net varlık"
          value={short(b.net_assets_usd)}
          tone={b.net_assets_usd < 0 ? "critical" : "neutral"}
          expected="vergi bunun üzerinden"
          title={usd(b.net_assets_usd)}
        />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Vergi senaryoları · {data.year}</h2>
          <button className="btn primary" onClick={() => setScenario("new")}>
            Senaryo ekle
          </button>
        </div>
        <div className="card-body">
          <p className="muted fin-note">
            Matrah = kalan net varlık − yıl sonuna kadar ek gider − daha önce vergilenmiş birikim. Eyalet vergisi önce hesaplanır ve
            federal matrahtan düşülür. Yıl içinde IRS'e ödenen {usd(b.paid_usd)} ("Vergi / KDV" kategorisi) kalandan düşülür.
          </p>
          {data.scenarios.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Senaryo</th>
                    <th className="num">Federal</th>
                    <th className="num">Eyalet</th>
                    <th className="num">Matrah</th>
                    <th className="num">Toplam vergi</th>
                    <th className="num">Ödenen</th>
                    <th className="num">Kalan ödenecek</th>
                    <th className="num">Vergi sonrası</th>
                    <th className="num">Efektif</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.scenarios.map((s) => (
                    <tr key={s.id} style={{ opacity: s.active ? 1 : 0.55 }}>
                      <td className="fin-desc">
                        <b>{s.name}</b>
                        {s.primary ? <span className="badge good">bilançoda</span> : null}
                        {!s.active ? <span className="badge">pasif</span> : null}
                        {s.extra_deductions || s.taxed_base ? (
                          <div className="sub">
                            {s.extra_deductions ? `ek gider ${usd(s.extra_deductions)}` : ""}
                            {s.extra_deductions && s.taxed_base ? " · " : ""}
                            {s.taxed_base ? `vergilenmiş ${usd(s.taxed_base)}` : ""}
                          </div>
                        ) : null}
                        {s.notes ? <div className="sub">{s.notes}</div> : null}
                      </td>
                      <td className="num">
                        {pct(s.federal_rate)}
                        <div className="sub">{usd(s.result.federal_usd)}</div>
                      </td>
                      <td className="num">
                        {pct(s.state_rate)}
                        <div className="sub">{usd(s.result.state_usd)}</div>
                      </td>
                      <td className="num">{usd(s.result.taxable_usd)}</td>
                      <td className="num">
                        <b>{usd(s.result.total_usd)}</b>
                      </td>
                      <td className="num">{usd(s.result.paid_usd)}</td>
                      <td className="num">
                        <b>{usd(s.result.remaining_usd)}</b>
                      </td>
                      <td className="num">{usd(s.result.after_tax_usd)}</td>
                      <td className="num">{pct(s.result.effective_rate)}</td>
                      <td>
                        <button className="btn sm" onClick={() => setScenario(s)}>
                          Düzenle
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Senaryo yok. Örneğin federal %21 ile başla.</div>
          )}
          <p className="muted fin-note">
            Yeni haftalık bilançoda ilk aktif senaryonun kalan vergisi "Ödenecek vergi" satırı, cüzdanlar da borç satırı olarak gelir.
          </p>
        </div>
      </section>

      <div className="grid-2 fin-wallets">
        {(["seller", "producer"] as WalletKind[]).map((kind) => (
          <WalletCard key={kind} kind={kind} wallets={data.wallets.filter((w) => w.kind === kind)} onAdd={() => setWallet(kind)} onEdit={setWallet} />
        ))}
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Satıcı cüzdan yüklemeleri</h2>
          <span className="muted">
            {day(data.start)} – {day(data.end)}
          </span>
        </div>
        <div className="card-body">
          <p className="muted fin-note">
            Açıklamasında "REX-…" müşteri numarası olan LLC girişleri ve Stripe tahsilatları otomatik "Satıcı cüzdan yüklemesi"
            sayılır; gelir değildir. Bakiye platformda harcandıkça düştüğü için buradan hesaplanmaz, yukarıya elle girilir.
          </p>
          <div className="tiles fin-tiles">
            <StatTile label="Yükleme" value={short(dep.usd)} title={usd(dep.usd)} expected={`${dep.count.toLocaleString("tr-TR")} işlem`} />
            <StatTile label="Satıcı" value={dep.customers.toLocaleString("tr-TR")} expected="farklı REX numarası" />
            <StatTile label="Numarasız" value={dep.without_ref.toLocaleString("tr-TR")} expected="Stripe vb., müşteri bilinmiyor" />
            <StatTile label="İade / chargeback" value={short(dep.refunds_usd)} title={usd(dep.refunds_usd)} expected="cüzdan kategorisindeki çıkış" />
          </div>
          <div className="grid-2">
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>En çok yükleyen</th>
                    <th className="num">Tutar</th>
                    <th className="num">Adet</th>
                    <th>Son</th>
                  </tr>
                </thead>
                <tbody>
                  {dep.top.map((c) => (
                    <tr key={c.ref}>
                      <td className="fin-desc">
                        <b>{c.ref}</b>
                        <div className="sub">{c.name || "—"}</div>
                      </td>
                      <td className="num">{usd(c.usd)}</td>
                      <td className="num">{c.count}</td>
                      <td>{day(c.last)}</td>
                    </tr>
                  ))}
                  {!dep.top.length ? (
                    <tr>
                      <td colSpan={4} className="empty">
                        Bu aralıkta yükleme yok.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Kanal</th>
                    <th className="num">Tutar</th>
                    <th className="num">Adet</th>
                  </tr>
                </thead>
                <tbody>
                  {dep.channels.map((c) => (
                    <tr key={c.account_id}>
                      <td>{c.name}</td>
                      <td className="num">{usd(c.usd)}</td>
                      <td className="num">{c.count.toLocaleString("tr-TR")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {wallet ? (
        <WalletForm
          wallet={typeof wallet === "string" ? null : wallet}
          kind={typeof wallet === "string" ? wallet : wallet.kind}
          onClose={() => setWallet(null)}
          onSaved={() => {
            setWallet(null);
            refresh();
          }}
        />
      ) : null}
      {scenario ? (
        <ScenarioForm
          scenario={scenario === "new" ? null : scenario}
          onClose={() => setScenario(null)}
          onSaved={() => {
            setScenario(null);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}

function WalletCard({
  kind,
  wallets,
  onAdd,
  onEdit,
}: {
  kind: WalletKind;
  wallets: Wallet[];
  onAdd: () => void;
  onEdit: (w: Wallet) => void;
}) {
  const total = wallets.filter((w) => w.active).reduce((s, w) => s + w.balance_usd, 0);
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {KIND_TITLE[kind]} <span className="muted">· {usd(total)}</span>
        </h2>
        <button className="btn sm" onClick={onAdd}>
          Ekle
        </button>
      </div>
      <div className="card-body">
        <p className="muted fin-note">{KIND_HINT[kind]}</p>
        {wallets.length ? (
          <div className="table-wrap">
            <table className="board fin-table">
              <thead>
                <tr>
                  <th>Ad</th>
                  <th className="num">Bakiye</th>
                  <th>Tarih</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {wallets.map((w) => (
                  <tr key={w.id} style={{ opacity: w.active ? 1 : 0.55 }}>
                    <td className="fin-desc">
                      {w.name}
                      {w.notes ? <div className="sub">{w.notes}</div> : null}
                    </td>
                    <td className="num">
                      {money(w.balance, w.currency)}
                      {w.currency !== "USD" ? <div className="sub">{usd(w.balance_usd)}</div> : null}
                    </td>
                    <td>{day(w.as_of)}</td>
                    <td>
                      <button className="btn sm" onClick={() => onEdit(w)}>
                        Düzenle
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">Henüz bakiye girilmedi.</div>
        )}
      </div>
    </section>
  );
}

function useSave(onSaved: () => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run(path: string, method: string, body?: unknown) {
    setBusy(true);
    setErr(null);
    try {
      await api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }
  return { busy, err, setErr, run };
}

function WalletForm({ wallet, kind, onClose, onSaved }: { wallet: Wallet | null; kind: WalletKind; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    name: wallet?.name ?? (kind === "seller" ? "Toplam" : ""),
    balance: wallet ? String(wallet.balance) : "",
    currency: wallet?.currency ?? "USD",
    as_of: wallet?.as_of ?? istanbulToday(),
    notes: wallet?.notes ?? "",
    active: wallet?.active ?? true,
  });
  const { busy, err, setErr, run } = useSave(onSaved);
  const set = (k: keyof typeof f, v: string | boolean) => setF({ ...f, [k]: v });

  function save() {
    if (!f.name.trim()) return setErr(kind === "seller" ? "Ad gerekli (ör. Toplam)." : "Üretici adı gerekli.");
    if (f.balance === "" || Number.isNaN(Number(f.balance))) return setErr("Bakiye gerekli.");
    const body = {
      name: f.name.trim(),
      balance: f.balance,
      currency: f.currency,
      as_of: f.as_of || null,
      notes: f.notes.trim() || null,
      active: f.active,
    };
    void run(wallet ? `/api/finance/wallets/${wallet.id}` : "/api/finance/wallets", wallet ? "PATCH" : "POST", wallet ? body : { ...body, company_id: COMPANY, kind });
  }

  function remove() {
    if (wallet && window.confirm(`${wallet.name} silinsin mi?`)) void run(`/api/finance/wallets/${wallet.id}`, "DELETE");
  }

  return (
    <Modal title={`${KIND_TITLE[kind]} · ${wallet ? "düzenle" : "ekle"}`} onClose={onClose}>
      <div className="f-grid">
        <Field label={kind === "seller" ? "Ad" : "Üretici"}>
          <input className="f-input" value={f.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Bakiye" hint={kind === "producer" ? "ödenecek +, verilen avans −" : undefined}>
          <input className="f-input num" type="number" step="0.01" value={f.balance} onChange={(e) => set("balance", e.target.value)} />
        </Field>
        <Field label="Para birimi">
          <select className="f-input" value={f.currency} onChange={(e) => set("currency", e.target.value)}>
            {CURRENCIES.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Bakiye tarihi">
          <input className="f-input" type="date" value={f.as_of} onChange={(e) => set("as_of", e.target.value)} />
        </Field>
      </div>
      <Field label="Not">
        <input className="f-input" value={f.notes} onChange={(e) => set("notes", e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={f.active} onChange={(e) => set("active", e.target.checked)} />
        Hesaba kat
      </label>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        {wallet ? (
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

const toPct = (v: number) => String(Math.round(v * 10000) / 100);

function ScenarioForm({ scenario, onClose, onSaved }: { scenario: TaxScenario | null; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({
    name: scenario?.name ?? "",
    federal: scenario ? toPct(scenario.federal_rate) : "21",
    state: scenario ? toPct(scenario.state_rate) : "0",
    extra: scenario ? String(scenario.extra_deductions) : "0",
    taxed: scenario ? String(scenario.taxed_base) : "0",
    notes: scenario?.notes ?? "",
    sort: scenario ? String(scenario.sort) : "10",
    active: scenario?.active ?? true,
  });
  const { busy, err, setErr, run } = useSave(onSaved);
  const set = (k: keyof typeof f, v: string | boolean) => setF({ ...f, [k]: v });
  const rate = (v: string) => (Number(v) / 100).toFixed(4);

  function save() {
    if (!f.name.trim()) return setErr("Senaryo adı gerekli.");
    const bad = [f.federal, f.state].some((v) => v === "" || Number(v) < 0 || Number(v) > 100);
    if (bad) return setErr("Oranlar 0 ile 100 arasında olmalı.");
    const body = {
      name: f.name.trim(),
      federal_rate: rate(f.federal),
      state_rate: rate(f.state),
      extra_deductions: f.extra || "0",
      taxed_base: f.taxed || "0",
      notes: f.notes.trim() || null,
      sort: Number(f.sort) || 0,
      active: f.active,
    };
    void run(scenario ? `/api/finance/tax-scenarios/${scenario.id}` : "/api/finance/tax-scenarios", scenario ? "PATCH" : "POST", scenario ? body : { ...body, company_id: COMPANY });
  }

  function remove() {
    if (scenario && window.confirm(`${scenario.name} silinsin mi?`)) void run(`/api/finance/tax-scenarios/${scenario.id}`, "DELETE");
  }

  return (
    <Modal title={scenario ? "Senaryoyu düzenle" : "Senaryo ekle"} onClose={onClose} wide>
      <div className="f-grid">
        <Field label="Ad">
          <input className="f-input" value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="ör. Federal %21 + eyalet" />
        </Field>
        <Field label="Sıra" hint="en küçük sıradaki aktif senaryo bilançoya girer">
          <input className="f-input num" type="number" value={f.sort} onChange={(e) => set("sort", e.target.value)} />
        </Field>
        <Field label="Federal oran (%)">
          <input className="f-input num" type="number" step="0.1" min={0} max={100} value={f.federal} onChange={(e) => set("federal", e.target.value)} />
        </Field>
        <Field label="Eyalet oranı (%)">
          <input className="f-input num" type="number" step="0.1" min={0} max={100} value={f.state} onChange={(e) => set("state", e.target.value)} />
        </Field>
        <Field label="Yıl sonuna kadar ek gider (USD)" hint="matrahtan düşülür">
          <input className="f-input num" type="number" step={1000} value={f.extra} onChange={(e) => set("extra", e.target.value)} />
        </Field>
        <Field label="Daha önce vergilenmiş birikim (USD)" hint="geçmiş yıllarda vergisi ödenmiş varlık">
          <input className="f-input num" type="number" step={1000} value={f.taxed} onChange={(e) => set("taxed", e.target.value)} />
        </Field>
      </div>
      <Field label="Not">
        <input className="f-input" value={f.notes} onChange={(e) => set("notes", e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={f.active} onChange={(e) => set("active", e.target.checked)} />
        Aktif
      </label>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        {scenario ? (
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
