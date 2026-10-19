import { Fragment, useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, istanbulToday, money, short, usd } from "./format";
import type { TaxPayload, TaxStructure, Wallet, WalletKind } from "./types";
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
  const [editing, setEditing] = useState(false);
  const { data, error } = useFin<TaxPayload>(`/api/finance/tax?company=${COMPANY}&start=${start}&end=${end}`, tick + local);
  const refresh = () => setLocal((n) => n + 1);
  if (!data) return <Loading error={error} />;

  const b = data.base;
  const sellers = data.wallets.filter((w) => w.kind === "seller");
  const noSeller = !sellers.some((w) => w.active);
  const dep = data.deposits;
  const prof = data.profit;
  const pay = data.payments;
  const st = data.settings;
  const shareGap = 1 - st.partners.reduce((s, p) => s + p.share, 0);

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
          expected="kâr bunun değişiminden"
          title={usd(b.net_assets_usd)}
        />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Yılın kârı ve ABD vergisi · {data.year}</h2>
          <button className="btn primary" onClick={() => setEditing(true)}>
            Vergi ayarları
          </button>
        </div>
        <div className="card-body">
          {prof ? (
            <>
              <div className="tiles fin-tiles">
                <StatTile label="Kâr (yıl başından)" value={short(prof.ytd_usd)} title={usd(prof.ytd_usd)} expected={`${prof.elapsed_days}. gün`} />
                <StatTile label="Yıl sonu tahmini kâr" value={short(prof.projected_usd)} title={usd(prof.projected_usd)} expected="aynı hızla devam ederse" />
                <StatTile label="Ortak ödemeleri" value={short(pay.partners_usd)} title={usd(pay.partners_usd)} expected="guaranteed payment, gider" />
                <StatTile label="Yüklenici ödemeleri" value={short(pay.contractors_usd)} title={usd(pay.contractors_usd)} expected="contractor, gider" />
              </div>
              <p className="muted fin-note">
                Kâr = bugünkü net varlık − 1 Ocak net varlığı ({usd(st.year_start_net_assets)})
                {st.distributions ? ` + kâr dağıtımı ${usd(st.distributions)}` : ""}. Ortak ve yüklenici ödemeleri gider olduğu için net
                varlığı zaten düşürür, geri eklenmez. Ödeme toplamları {day(pay.since)} sonrası kayıtlı hareketlerden.
                {pay.irs_usd ? ` Bu yıl IRS'e ödenen ${usd(pay.irs_usd)} geçen yılın vergisi; bu yılın hesabına girmez.` : ""}
              </p>
              <div className="table-wrap">
                <table className="board fin-table">
                  <thead>
                    <tr>
                      <th>Yapı</th>
                      <th className="num">ABD federal vergisi</th>
                      <th className="num">Kâra oranı</th>
                      <th className="num">Vergi sonrası</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.scenarios.map((s) => (
                      <Fragment key={s.key}>
                        <tr>
                          <td className="fin-desc">
                            <b>{s.title}</b>
                            {s.current ? <span className="badge good">bugünkü yapı</span> : null}
                            <div className="sub">{s.note}</div>
                          </td>
                          <td className="num">
                            <b>{usd(s.tax_usd)}</b>
                          </td>
                          <td className="num">{pct(s.rate)}</td>
                          <td className="num">{usd(Math.max(prof.projected_usd, 0) - s.tax_usd)}</td>
                        </tr>
                        {s.partners.map((p) => (
                          <tr key={`${s.key}-${p.name}`} className="fin-sub">
                            <td>
                              {p.name} <span className="muted">· pay {pct(p.share)} · kâr payı {usd(p.income_usd)}</span>
                            </td>
                            <td className="num">{usd(p.tax_usd)}</td>
                            <td className="num">{p.income_usd > 0 ? pct(p.tax_usd / p.income_usd) : "—"}</td>
                            <td />
                          </tr>
                        ))}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
              {shareGap > 0.0001 ? (
                <div className="banner">Ortak payları toplamı {pct(1 - shareGap)}; kalan {pct(shareGap)} "Atanmamış pay" olarak hesaplandı.</div>
              ) : null}
              <p className="muted fin-note">
                Yıl sonu tahmini kâr üzerinden, 2026 tarifesiyle. Eyalet vergisi yok (Delaware, eyalette faaliyet yok; yıllık 300 $ LLC
                vergisi ayrı). FDII oranı{" "}
                {st.fdii_share === null ? `REX yüklemelerinden tahmin (ABD şirketi görünmeyen satıcılar ${pct(data.foreign_share)})` : `elle ${pct(st.fdii_share)}`}
                . Rakamlar karar desteğidir; uygulamadan önce CPA ile doğrulayın.
              </p>
            </>
          ) : (
            <div className="empty">
              Kâr hesabı için 1 Ocak {data.year}'teki net varlık gerekli (hesaplar − satıcı cüzdanları − üretici bakiyeleri).{" "}
              <button className="btn sm" onClick={() => setEditing(true)}>
                Vergi ayarlarını gir
              </button>
            </div>
          )}
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
      {editing ? (
        <SettingsForm
          data={data}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
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

const STRUCTURE_LABEL: Record<TaxStructure, string> = {
  partnership: "Ortaklık · ABD faaliyetine bağlı (bugünkü beyan)",
  none: "Ortaklık · ABD'de faaliyet yok",
  ccorp: "C-Corp",
};
const toPct = (v: number | null) => (v === null ? "" : String(Math.round(v * 10000) / 100));
const fromPct = (v: string) => (Number(v) / 100).toFixed(4);
const badPct = (v: string) => v !== "" && (Number.isNaN(Number(v)) || Number(v) < 0 || Number(v) > 100);

function SettingsForm({ data, onClose, onSaved }: { data: TaxPayload; onClose: () => void; onSaved: () => void }) {
  const st = data.settings;
  const [f, setF] = useState({
    start: st.year_start_net_assets === null ? "" : String(st.year_start_net_assets),
    distributions: String(st.distributions),
    structure: st.structure,
    dividend: toPct(st.dividend_ratio),
    fdii: toPct(st.fdii_share),
  });
  const [partners, setPartners] = useState(st.partners.map((p) => ({ name: p.name, share: toPct(p.share) })));
  const { busy, err, setErr, run } = useSave(onSaved);
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  const total = partners.reduce((s, p) => s + (Number(p.share) || 0), 0);

  function save() {
    if (f.start !== "" && Number.isNaN(Number(f.start))) return setErr("1 Ocak net varlığı sayı olmalı.");
    if (badPct(f.dividend) || badPct(f.fdii) || partners.some((p) => badPct(p.share))) return setErr("Oranlar 0 ile 100 arasında olmalı.");
    if (total > 100.0001) return setErr("Ortak payları toplamı %100'ü geçemez.");
    if (partners.some((p) => !p.name.trim())) return setErr("Ortak adı boş olamaz.");
    void run(`/api/finance/tax-settings/${COMPANY}`, "PUT", {
      year_start_net_assets: f.start === "" ? null : f.start,
      distributions: f.distributions || "0",
      contributions: "0",
      structure: f.structure,
      dividend_ratio: fromPct(f.dividend || "0"),
      fdii_share: f.fdii === "" ? null : fromPct(f.fdii),
      partners: partners.map((p) => ({ name: p.name.trim(), share: fromPct(p.share || "0") })),
    });
  }

  return (
    <Modal title="Vergi ayarları · Rexven LLC" onClose={onClose} wide>
      <div className="f-grid">
        <Field label={`1 Ocak ${data.year} net varlık (USD)`} hint="o günkü hesaplar − satıcı cüzdanları − üretici bakiyeleri">
          <input className="f-input num" type="number" step={1000} value={f.start} onChange={(e) => set("start", e.target.value)} />
        </Field>
        <Field label="Yıl içi kâr dağıtımı (USD)" hint="guaranteed payment değil; yalnız kâr payı dağıtımı">
          <input className="f-input num" type="number" step={1000} value={f.distributions} onChange={(e) => set("distributions", e.target.value)} />
        </Field>
        <Field label="Bugünkü yapı" hint="bilançodaki tahmini vergi satırı buna göre">
          <select className="f-input" value={f.structure} onChange={(e) => set("structure", e.target.value)}>
            {(Object.keys(STRUCTURE_LABEL) as TaxStructure[]).map((k) => (
              <option key={k} value={k}>
                {STRUCTURE_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="C-Corp'ta dağıtılacak kâr (%)" hint="vergi sonrası kârın ortaklara kâr payı olarak dağıtılan kısmı">
          <input className="f-input num" type="number" step={5} min={0} max={100} value={f.dividend} onChange={(e) => set("dividend", e.target.value)} />
        </Field>
        <Field
          label="Yurtdışı müşteri payı, FDII (%)"
          hint={`boş bırakılırsa REX yüklemelerinden tahmin${data.foreign_share_estimate === null ? "" : `: ${pct(data.foreign_share_estimate)}`}`}
        >
          <input className="f-input num" type="number" step={5} min={0} max={100} value={f.fdii} onChange={(e) => set("fdii", e.target.value)} />
        </Field>
      </div>

      <div className="fin-h3">
        Ortaklar <span className="muted">· toplam %{new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 }).format(total)}</span>
      </div>
      {partners.map((p, i) => (
        <div key={i} className="f-grid fin-partner">
          <input className="f-input" value={p.name} placeholder="Ad soyad" aria-label="Ortak adı" onChange={(e) => setPartners(partners.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} />
          <input className="f-input num" type="number" step={0.5} min={0} max={100} value={p.share} placeholder="pay %" aria-label="Pay yüzdesi" onChange={(e) => setPartners(partners.map((x, j) => (j === i ? { ...x, share: e.target.value } : x)))} />
          <button className="btn sm" onClick={() => setPartners(partners.filter((_, j) => j !== i))}>
            Kaldır
          </button>
        </div>
      ))}
      <button className="btn sm" onClick={() => setPartners([...partners, { name: "", share: "" }])}>
        Ortak ekle
      </button>

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
