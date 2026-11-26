import { Fragment, useState, type ChangeEvent } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { COMPANY_SHORT, money, short, usd } from "./format";
import { PayrollTab } from "./PayrollTab";
import type { CompanyId, CompassParams, CompassPayload, CompassPerson, CompassPersonPlan } from "./types";
import { CompanyTag, Field, FxNote, Loading, Modal, useFin, type TabProps } from "./ui";

const nf = (d: number) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: d });
/** %30 / %8,9 */
const pct = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : `%${nf(d).format(v * 100)}`);
const pts = (v: number) => `${v > 0 ? "+" : ""}${nf(1).format(v * 100)} puan`;
const change = (a: number, b: number) => (a ? b / a - 1 : 0);

/** Maaş ve prim pusulası: 2027 zammını kurallarla önerir, gerekçesini ve geçmiş prim yükünü gösterir. */
export function CompassTab(props: TabProps) {
  const { scope, tick } = props;
  const [local, setLocal] = useState(0);
  const [editing, setEditing] = useState<CompassPerson | null>(null);
  const { data, error } = useFin<CompassPayload>(`/api/finance/compass?company=${scope}`, tick + local);
  if (!data) return <Loading error={error} />;
  const reload = () => setLocal((n) => n + 1);

  async function savePlan(params: CompassParams, people = data!.plan_people) {
    await api("/api/finance/compass/plan", { method: "PUT", body: JSON.stringify({ params, people }) });
    reload();
  }

  const t = data.totals;
  const h = data.history;
  const primMonthly = h.prim_2026_months ? h.prim_2026_usd / h.prim_2026_months : 0;
  const delta = t.cost27_usd - t.cost_usd;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <p className="muted cp-lead">
        Bu ekran maaş takibi için değil, {data.year} maaşlarını ve yeni prim sistemini belirlemek için. Kurallar aşağıda; kural değişince her kişinin
        önerisi, gerekçesi ve toplam maliyet yeniden hesaplanır. Tutarlar aylık; USD karşılıkları bugünkü kurla, {data.year} maliyeti {data.year} ortası
        beklenen kurla (USD/TRY {nf(1).format(data.derived.usdtry_27 ?? 0)}).
      </p>

      <div className="tiles fin-tiles">
        <StatTile label="Bugünkü maaş yükü" value={short(t.cost_usd)} title={usd(t.cost_usd)} expected={`aylık · ${t.headcount} kişi · yıllık ${short(t.cost_usd * 12)}`} />
        <StatTile
          label={`${data.year} önerisi`}
          value={short(t.cost27_usd)}
          title={usd(t.cost27_usd)}
          expected={`aylık · USD'de ${pct(change(t.cost_usd, t.cost27_usd))} · yılda +${short(delta * 12)}`}
        />
        <StatTile
          label="2026 prim yükü"
          value={short(h.prim_2026_usd)}
          title={usd(h.prim_2026_usd)}
          expected={`${h.prim_2026_months} ay · aylık ${short(primMonthly)} · maaş yükünün ${pct(primMonthly / t.cost_usd, 0)}'i`}
        />
        <StatTile label="2026 donation" value={short(h.donation_2026_usd)} title={usd(h.donation_2026_usd)} expected={`${h.prim_2026_months} ay · kârın ${pct(h.donation_2026_usd / h.kar_2026_usd)}'i`} />
      </div>

      <History data={data} />
      <Rules data={data} onSave={savePlan} />
      <Companies data={data} />
      <CLevel data={data} onEdit={setEditing} />
      <SalaryList data={data} onEdit={setEditing} />
      <Seniority data={data} />
      <Bonus data={data} />

      <details className="card cp-details">
        <summary className="card-head">
          <h2>Maaş yükü hesabı ve ödeme kayıtları</h2>
          <span className="muted">net → brüt → işveren maliyeti; kayıt ekle / düzelt</span>
        </summary>
        <div className="card-body">
          <PayrollTab {...props} tick={tick + local} />
        </div>
      </details>

      {editing ? (
        <PersonPlanForm
          person={editing}
          onClose={() => setEditing(null)}
          onSave={async (plan) => {
            const people = { ...data.plan_people };
            if (plan.pct === null && !plan.adj && !plan.note) delete people[editing.person];
            else people[editing.person] = plan;
            await savePlan(data.params, people);
            setEditing(null);
          }}
        />
      ) : null}
    </>
  );
}

// --- geçmiş: çeyrek bazında prim ve donation ------------------------------------------

function History({ data }: { data: CompassPayload }) {
  const h = data.history;
  const sum = (k: "kar_usd" | "havuz_prim_usd" | "satis_prim_usd" | "prim_usd" | "donation_usd") => h.quarters.reduce((a, q) => a + q[k], 0);
  const q4 = h.q4_2025;
  const realMedian = h.raise_2026_median === null ? null : (1 + h.raise_2026_median) / 1.309 - 1;
  const realWeighted = h.raise_2026_weighted === null ? null : (1 + h.raise_2026_weighted) / 1.309 - 1;
  const primShare = h.kar_2026_usd ? h.prim_2026_usd / h.kar_2026_usd : 0;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Nereden geliyoruz: prim, donation ve 2026 zammı</h2>
        <span className="muted">prim tablosundan · USD, TL tutarlar o ayın kuruyla</span>
      </div>
      <div className="card-body">
        <div className="table-wrap">
          <table className="board fin-table">
            <thead>
              <tr>
                <th>Çeyrek</th>
                <th className="num">Rexven kârı</th>
                <th className="num">Havuz primi</th>
                <th className="num">Satış primi</th>
                <th className="num">Toplam prim</th>
                <th className="num">Prim / kâr</th>
                <th className="num">Donation</th>
                <th className="num">Donation / kâr</th>
              </tr>
            </thead>
            <tbody>
              {h.quarters.map((q) => (
                <tr key={q.quarter} className={q.quarter === q4?.quarter ? "cp-hl" : undefined}>
                  <td>
                    <b>{q.quarter}</b>
                    {q.months < 3 ? <span className="muted"> ({q.months} ay)</span> : null}
                  </td>
                  <td className="num">{usd(q.kar_usd)}</td>
                  <td className="num">{usd(q.havuz_prim_usd)}</td>
                  <td className="num">{q.satis_prim_usd ? usd(q.satis_prim_usd) : "—"}</td>
                  <td className="num">
                    <b>{usd(q.prim_usd)}</b>
                  </td>
                  <td className="num">{pct(q.prim_usd / q.kar_usd)}</td>
                  <td className="num">{usd(q.donation_usd)}</td>
                  <td className="num">{pct(q.donation_usd / q.kar_usd)}</td>
                </tr>
              ))}
              <tr className="pay-total">
                <td>
                  <b>Toplam</b>
                </td>
                <td className="num">{usd(sum("kar_usd"))}</td>
                <td className="num">{usd(sum("havuz_prim_usd"))}</td>
                <td className="num">{usd(sum("satis_prim_usd"))}</td>
                <td className="num">
                  <b>{usd(sum("prim_usd"))}</b>
                </td>
                <td className="num">{pct(sum("prim_usd") / sum("kar_usd"))}</td>
                <td className="num">
                  <b>{usd(sum("donation_usd"))}</b>
                </td>
                <td className="num">{pct(sum("donation_usd") / sum("kar_usd"))}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ul className="cp-points">
          {q4 ? (
            <li>
              <b>2025 son çeyrek (Q4) prim yükü {usd(q4.prim_usd)}</b>: ayda ortalama {usd(q4.prim_usd / 3)}, kârın {pct(q4.prim_usd / q4.kar_usd)}'i.
              Aynı çeyrekte {usd(q4.donation_usd)} donation dağıtıldı.
            </li>
          ) : null}
          <li>
            <b>
              2026'da (Q1–Q3) {usd(h.prim_2026_usd)} prim
            </b>{" "}
            dağıtıldı: ayda {usd(h.prim_2026_usd / Math.max(1, h.prim_2026_months))}, kârın {pct(primShare)}'i ve bugünkü maaş yükünün{" "}
            {pct(h.prim_2026_usd / Math.max(1, h.prim_2026_months) / data.totals.cost_usd, 0)}'i. Havuz kârın %10'u; fark ayrı performans primi ve satış
            primi. Ekim–Kasım tabloya girilince Q4 satırı oluşur; Aralık'tan itibaren yeni sistem.
          </li>
          <li>
            <b>2026 zammı</b> (Aralık 2025 → Ocak 2026, maaş + multinet, C-Level hariç {h.raise_2026_count} kişi): ortanca {pct(h.raise_2026_median, 0)},
            toplam bordroda {pct(h.raise_2026_weighted, 0)}. 2025 TÜFE'si %30,9 olduğundan reel artış ortancada {pct(realMedian, 0)}, toplamda{" "}
            {pct(realWeighted, 0)}. Yani 2026'da enflasyonun üstünde zam yapıldı; 2027'de tabanın enflasyon olması alım gücünü korumaya yeter.
          </li>
          <li>
            <b>Donation</b> kârdan ayrı bir pay (Eylül 2025'e kadar %5, sonra %6); 2026'da {usd(h.donation_2026_usd)}. Maaş ve primden bağımsız, maliyete dahil
            edilmedi.
          </li>
        </ul>
      </div>
    </section>
  );
}

// --- kurallar -------------------------------------------------------------------------

function Rules({ data, onSave }: { data: CompassPayload; onSave: (p: CompassParams) => Promise<void> }) {
  const p = data.params;
  const d = data.derived;
  const [f, setF] = useState({
    tr: String(p.tr * 100),
    de: String(p.de * 100),
    asgari: p.asgari === null ? "" : String(p.asgari * 100),
    kur_artis: p.kur_artis === null ? "" : String(p.kur_artis * 100),
    c_level_usd: p.c_level_usd === null ? "" : String(p.c_level_usd * 100),
    kidem_step: String(p.kidem_step * 100),
    kidem_cap: String(p.kidem_cap),
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const num = (s: string) => Number(s.replace(",", ".")) / 100;
  const opt = (s: string) => (s.trim() === "" ? null : num(s));
  const set = (k: keyof typeof f) => (e: ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await onSave({
        tr: num(f.tr),
        de: num(f.de),
        asgari: opt(f.asgari),
        kur_artis: opt(f.kur_artis),
        c_level_usd: opt(f.c_level_usd),
        kidem_step: num(f.kidem_step),
        kidem_cap: Math.round(Number(f.kidem_cap)),
      });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    }
    setBusy(false);
  }

  const rules: { key: keyof typeof f; title: string; unit: string; placeholder?: string; why: string }[] = [
    {
      key: "tr",
      title: "Türkiye tabanı (TÜFE)",
      unit: "%",
      why: "AŞ bordrosu ve LLC'deki TL sözleşmeler. 2026 TÜFE Ekim'de yıllık %29,77; kesin rakam 3 Ocak'ta açıklanır, gelince buraya yazılır. Türkiye'de yaşayanın alım gücünü korur.",
    },
    {
      key: "de",
      title: "Almanya tabanı",
      unit: "%",
      why: "GmbH (Almanya'da yaşayanlar). Almanya enflasyonu Ekim 2026'da %2,8; toplu sözleşme artışları %3–5 bandında. Enflasyon + ~1 puan reel.",
    },
    {
      key: "asgari",
      title: "Asgari ücret artışı",
      unit: "%",
      placeholder: `TÜFE (${pct(p.tr, 0)})`,
      why: `Asgari ücretli bordro, asgari ücret + döviz paketlerinin AŞ payı, Bağ-Kur. Yasal artışı izler; Aralık'ta açıklanınca girilir. 2027 net asgari ≈ ${money(d.asgari_net_27, "TRY")}.`,
    },
    {
      key: "kur_artis",
      title: "TL'nin yıllık değer kaybı",
      unit: "%",
      placeholder: `son 12 ay ${pct(d.kur_artis_observed)}`,
      why: `USD/TRY bugün ${nf(2).format(d.usdtry_now ?? 0)}. Türkiye'de yaşayıp dövizle ödenen (LLC USD/EUR) artışı TL karşılığı TÜFE kadar büyüyecek şekilde: (1 + TÜFE) / (1 + değer kaybı) − 1 = ${pct(d.fx_rule)}.`,
    },
    {
      key: "kidem_step",
      title: "Kıdem payı (her tam yıl)",
      unit: "puan",
      why: "İlk yıldan sonraki her tam yıl için tabana eklenir; C-Level hariç. Bağlılığı ödüllendirir, maaşı enflasyondan koparmaz.",
    },
    { key: "kidem_cap", title: "Kıdem payı tavanı", unit: "yıl", why: "Bu kadar yıldan sonra kıdem payı artmaz (ör. 3 → en çok +3 puan, 4+ yıl kıdem)." },
    {
      key: "c_level_usd",
      title: "Kurucuların döviz paketi",
      unit: "%",
      placeholder: `alım gücü kuralı (${pct(d.fx_rule)})`,
      why: "Kurucu kararı. Boş bırakılırsa diğer döviz alanlarla aynı kural uygulanır. Ayrıntı C-Level bölümünde.",
    },
  ];

  return (
    <section className="card">
      <div className="card-head">
        <h2>{data.year} zam kuralları</h2>
        <span className="muted">1 Ocak {data.year}'de yürürlüğe girer · kesim 1 Ekim 2026 · yıl bazlı</span>
      </div>
      <div className="card-body">
        <div className="cp-rules">
          {rules.map((r) => (
            <div key={r.key} className="cp-rule">
              <label>
                <span className="cp-rule-title">{r.title}</span>
                <span className="cp-rule-input">
                  <input className="f-input num" inputMode="decimal" value={f[r.key]} placeholder={r.placeholder} onChange={set(r.key)} />
                  <span className="muted">{r.unit}</span>
                </span>
              </label>
              <p className="muted">{r.why}</p>
            </div>
          ))}
        </div>
        <ul className="cp-points">
          <li>
            <b>2026'da girenler</b>: maaşları giriş ayının fiyatlarıyla kuruldu; taban artış çalışılan aya oranlanır (Temmuz girişli 6/12). Kıdem payı ve
            kişiye özel düzeltme ayrıca eklenir.
          </li>
          <li>
            <b>Kişiye özel karar</b> (zam yok, eşitlik düzeltmesi, telafi) listedeki “Ayarla” ile girilir; gerekçesi satırda görünür.
          </li>
          <li>
            <b>Eski havuz primi maaşa katılmıyor</b>; prim Aralık'tan itibaren performansa bağlı yeni sistemle ayrı ödenir.
          </li>
        </ul>
        {err ? <div className="banner error">{err}</div> : null}
        <div className="f-actions">
          <div className="spacer" />
          <button className="btn primary" onClick={save} disabled={busy}>
            {busy ? "Hesaplanıyor…" : "Kuralları kaydet ve yeniden hesapla"}
          </button>
        </div>
      </div>
    </section>
  );
}

// --- şirket bazlı öneri ---------------------------------------------------------------

function companyWhy(id: CompanyId, data: CompassPayload): string {
  const d = data.derived;
  const p = data.params;
  if (id === "tr")
    return `Çalışanlar Türkiye'de, TL bordrolu. Taban TÜFE (${pct(p.tr, 0)}) + kıdem payı; asgari ücretliler yasal artışı alır. İşveren SGK payı (%21,75) ve kıdem karşılığı brütle büyür. TL yılda ~${pct(d.kur_artis, 0)} değer kaybettiği için USD maliyeti TL artışından az büyür.`;
  if (id === "llc")
    return `Ekip Türkiye'de yaşıyor. TL sözleşmeler TÜFE (${pct(p.tr, 0)}) + kıdem payı alır. Dövizle ödenenlerde artış TL karşılığını korur (${pct(d.fx_rule)}); asgari ücret + döviz paketi olanlarda AŞ payı yasal artışı alır, döviz payı kalanı tamamlar. Kişiye özel kararlar (zam yok vb.) satırında. Sözleşmede işveren yükü yok, maliyet ödenen tutar.`;
  return `Çalışanlar Almanya'da, EUR bordrolu. Taban ${pct(p.de, 0)} + kıdem payı; Türkiye oranı uygulanmaz çünkü Almanya enflasyonu %2,8. İşveren sosyal sigorta payı (~%21) ve %2 ek yük brütle birlikte artar. EUR/USD sabit varsayıldı.`;
}

function Companies({ data }: { data: CompassPayload }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Şirket bazlı öneri</h2>
        <span className="muted">aylık maliyet USD · ortalama zam yerel para biriminde</span>
      </div>
      <div className="card-body cp-cos">
        {data.companies.map((c) => {
          const diff = c.cost27_usd - c.cost_usd;
          return (
            <div key={c.company_id} className="cp-co">
              <div className="cp-co-head">
                <CompanyTag id={c.company_id} />
                <span className="muted">
                  {c.headcount} kişi{c.headcount !== c.staff ? ` (${c.headcount - c.staff} C-Level)` : ""}
                </span>
              </div>
              <div className="cp-co-rate">{pct(c.rate_local)}</div>
              <div className="muted">ortalama zam · ortanca {pct(c.median_rate)}</div>
              <div className="cp-co-cost">
                {usd(c.cost_usd)} → <b>{usd(c.cost27_usd)}</b> <span className="muted">({pct(change(c.cost_usd, c.cost27_usd))} USD)</span>
              </div>
              <div className="muted">yılda +{usd(diff * 12)}</div>
              <p>{companyWhy(c.company_id, data)}</p>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// --- C-Level --------------------------------------------------------------------------

function CLevel({ data, onEdit }: { data: CompassPayload; onEdit: (p: CompassPerson) => void }) {
  const rows = data.people.filter((p) => p.c_level);
  if (!rows.length) return null;
  const d = data.derived;
  const founders = rows.filter((p) => p.companies.includes("llc"));
  const de = rows.filter((p) => !p.companies.includes("llc"));
  const founderNow = founders.reduce((a, p) => a + p.value_usd, 0);
  const prim = rows.reduce((a, p) => a + (p.prim_2026_usd ?? 0), 0);
  return (
    <section className="card">
      <div className="card-head">
        <h2>C-Level</h2>
        <span className="muted">kıdem payı uygulanmaz · kurucu kararı</span>
      </div>
      <div className="card-body">
        <PeopleTable people={rows} onEdit={onEdit} />
        <ul className="cp-points">
          {founders.length ? (
            <li>
              <b>Kurucular ({founders.map((p) => p.person.split(" ")[0]).join(", ")})</b>: Türkiye'de yaşıyor, toplam paket kişi başı ~
              {usd(founderNow / founders.length)} (AŞ asgari payı + LLC USD). USD'yi sabit tutmak TL alım gücünü ~{pct(1 - 1 / (1 + d.fx_rule), 0)}{" "}
              düşürür (TÜFE {pct(data.params.tr, 0)}, TL değer kaybı {pct(d.kur_artis, 0)}). Seçenekler: <b>%0</b> (maliyet sabit, reel kayıp),{" "}
              <b>{pct(d.fx_rule)}</b> (alım gücü korunur; kurucu başı ayda +{usd((founderNow / founders.length) * d.fx_rule)}), ya da yarısı.{" "}
              <b>Öneri</b>: alım gücü kuralı; kurucuların asıl kazancı maaş değil kâr olduğu için maaşı enflasyonda tutup artı değeri kâr payına bırakmak
              ekibe de tutarlı bir mesaj verir.
            </li>
          ) : null}
          {de.length ? (
            <li>
              <b>GmbH C-Level ({de.map((p) => p.person.split(" ")[0]).join(", ")})</b>: Almanya'da yaşıyor. Almanya tabanı{" "}
              {pct(data.params.de, 0)} uygulanır; ekiple aynı kural, ayrıcalık yok.
            </li>
          ) : null}
          <li>
            <b>Prim</b>: C-Level 2026'da havuzdan en yüksek ağırlıkla (3) ayda toplam ~{usd(prim)} aldı. Yeni sistemde C-Level ekip havuzundan çıkıp şirket
            kârına bağlanmalı; ekip primi C-Level'a pay ayırmadan dağıtılır.
          </li>
        </ul>
      </div>
    </section>
  );
}

// --- maaş listesi ---------------------------------------------------------------------

function payText(l: CompassPerson["lines"][number], amount: number): string {
  const tag = l.label ? ` ${l.label}` : l.kind === "bordro" ? (l.basis === "brut" ? " brüt" : " net") : "";
  return `${money(amount, l.currency)}${tag}`;
}

function PeopleTable({ people, onEdit, grouped }: { people: CompassPerson[]; onEdit: (p: CompassPerson) => void; grouped?: boolean }) {
  let unit = "";
  return (
    <div className="table-wrap">
      <table className="board fin-table cp-list">
        <thead>
          <tr>
            <th>Kişi</th>
            <th>Kıdem</th>
            <th>Bugün</th>
            <th className="num" title="Aralık 2025 → Ocak 2026, maaş + multinet">
              2026 zammı
            </th>
            <th className="num" title="2026 aylık ortalama prim (havuz + satış)">
              2026 prim / ay
            </th>
            <th className="num">Öneri</th>
            <th>2027</th>
            <th className="num">Maliyet / ay</th>
            <th>Gerekçe</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {people.map((p) => {
            const head = grouped && p.unit !== unit;
            unit = p.unit;
            return (
              <Fragment key={p.person}>
                {head ? (
                  <tr className="cp-group">
                    <td colSpan={10}>{p.unit || "Birimsiz"}</td>
                  </tr>
                ) : null}
                <tr>
                  <td className="fin-desc">
                    <b>{p.person}</b>
                    {p.flags?.length ? (
                      <span className="badge warning" title={p.flags.join("\n")}>
                        kıdem
                      </span>
                    ) : null}
                    <div className="sub">{p.companies.map((c) => COMPANY_SHORT[c]).join(" + ")}</div>
                  </td>
                  <td>{p.kidem}</td>
                  <td>
                    {p.lines.map((l) => (
                      <div key={l.id}>{payText(l, l.amount)}</div>
                    ))}
                  </td>
                  <td className="num">{p.raise_2026 ? pct(p.raise_2026.rate, 0) : "—"}</td>
                  <td className="num">{p.prim_2026_usd ? usd(p.prim_2026_usd) : "—"}</td>
                  <td className="num">
                    <b>{pct(p.rate)}</b>
                  </td>
                  <td>
                    {p.lines.map((l) => (
                      <div key={l.id}>{payText(l, l.new_amount)}</div>
                    ))}
                  </td>
                  <td className="num">
                    {usd(p.cost_usd)} → {usd(p.cost27_usd)}
                  </td>
                  <td className="cp-why">
                    {p.reasons.map((r) => (
                      <div key={r}>{r}</div>
                    ))}
                    {p.flags?.map((r) => (
                      <div key={r} className="cp-flag">
                        {r}
                      </div>
                    ))}
                  </td>
                  <td>
                    <button className="btn sm" onClick={() => onEdit(p)}>
                      Ayarla
                    </button>
                  </td>
                </tr>
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SalaryList({ data, onEdit }: { data: CompassPayload; onEdit: (p: CompassPerson) => void }) {
  const staff = data.people.filter((p) => !p.c_level);
  const now = staff.reduce((a, p) => a + p.cost_usd, 0);
  const next = staff.reduce((a, p) => a + p.cost27_usd, 0);
  return (
    <section className="card">
      <div className="card-head">
        <h2>Maaş listesi ve {data.year} önerisi</h2>
        <span className="muted">
          C-Level hariç {staff.length} kişi · ortanca zam {pct(data.totals.staff_rate_median)} · maliyet {usd(now)} → {usd(next)}
        </span>
      </div>
      <div className="card-body">
        <PeopleTable people={staff} onEdit={onEdit} grouped />
        <p className="muted fin-note">
          Bugün: ödenen tutar (bordroda net ya da brüt, sözleşmede fatura). 2027: önerilen tutar, aynı biçimde. Maliyet: işverene aylık toplam (vergi,
          sigorta, yan hak, kıdem karşılığı dahil), USD. “kıdem” etiketi: aynı birimde daha kıdemsiz biri belirgin daha fazla alıyor.
        </p>
      </div>
    </section>
  );
}

// --- kıdem ----------------------------------------------------------------------------

function Seniority({ data }: { data: CompassPayload }) {
  const p = data.params;
  const flagged = data.people.filter((x) => x.flags?.length);
  const bands = [
    { label: "1 yıldan az", test: (y: number) => y < 1 },
    { label: "1–2 yıl", test: (y: number) => y >= 1 && y < 2 },
    { label: "2–4 yıl", test: (y: number) => y >= 2 && y < 4 },
    { label: "4+ yıl", test: (y: number) => y >= 4 },
  ].map((b) => ({ ...b, n: data.people.filter((x) => !x.c_level && x.start_date && b.test(x.kidem_years)).length }));
  return (
    <section className="card">
      <div className="card-head">
        <h2>Kıdeme göre maaş nasıl belirlenir</h2>
        <span className="muted">kıdem payının {data.year} maliyeti ~{usd(data.totals.kidem_cost27_usd)} / ay</span>
      </div>
      <div className="card-body">
        <div className="cp-bands">
          {bands.map((b) => (
            <div key={b.label}>
              <b>{b.n}</b>
              <span className="muted">{b.label}</span>
            </div>
          ))}
        </div>
        <ol className="cp-points">
          <li>
            <b>Kıdem zammın tabanını değiştirmez.</b> Herkes yaşadığı ülkenin enflasyonunu alır; kıdemli de yeni gelen de alım gücünü korur.
          </li>
          <li>
            <b>Kıdem payı tabana eklenir:</b> ilk yıldan sonraki her tam yıl için {pts(p.kidem_step)}, en çok {p.kidem_cap} yıl (+
            {nf(1).format(p.kidem_step * p.kidem_cap * 100)} puan). Bugünkü ekipte 4+ yıllıklar en çok, 2026 girişliler hiç almaz.
          </li>
          <li>
            <b>Yeni girenler oranlı alır:</b> 2026'da girenin maaşı giriş ayının fiyatıyla kuruldu; tam enflasyon farkı ona iki kez ödemek olur.
          </li>
          <li>
            <b>Kıdem rolün önüne geçmez:</b> Mentor-A ile Mentor-B, yazılımda kıdemli ile yeni farkı seviye farkıdır. Seviye atlama (terfi) zamdan ayrı bir
            karar; yılda bir, gerekçesiyle.
          </li>
          <li>
            <b>Sonraki adım: birim bandı.</b> Her birim için giriş, orta ve kıdemli maaş aralığı yazılırsa kıdem bant içindeki yeri belirler; yeni işe alım da
            bant başından başlar ve “kıdemsiz biri daha fazla alıyor” durumu oluşmaz.
          </li>
        </ol>
        {flagged.length ? (
          <>
            <h3 className="cp-sub">Kıdemle uyuşmayan maaşlar</h3>
            <ul className="cp-points">
              {flagged.map((x) => (
                <li key={x.person}>
                  <b>{x.person}</b> ({x.unit}, {x.kidem}): {x.flags!.join("; ")}. Fark seviyeden geliyorsa gerekçesi yazılsın; gelmiyorsa “Ayarla” ile eşitlik
                  düzeltmesi planlanabilir.
                </li>
              ))}
            </ul>
          </>
        ) : null}
      </div>
    </section>
  );
}

// --- prim -----------------------------------------------------------------------------

function Bonus({ data }: { data: CompassPayload }) {
  const h = data.history;
  const months = Math.max(1, h.prim_2026_months);
  const kar = h.kar_2026_usd / months;
  const budget = kar * 0.1;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Prim: Aralık'tan itibaren yeni sistem</h2>
        <span className="muted">ağırlıklar değişiyor · performansa bağlı</span>
      </div>
      <div className="card-body">
        <ol className="cp-points">
          <li>
            <b>Bütçe aynı kalsın: kârın %10'u.</b> 2026'da kâr ayda ortalama {usd(kar)}; havuz ~{usd(budget)} / ay, yılda ~{short(budget * 12)}. Yeni
            sistem toplam yükü artırmaz, dağıtımı değiştirir.
          </li>
          <li>
            <b>Herkes hedef primini yıl başında bilsin.</b> Eski sistemde kimse ne alacağını bilmiyordu; bu yüzden prim maaş gibi algılandı ve motive
            etmedi. Kişinin hedef primi = bütçeden rol ağırlığına düşen pay; gerçekleşme metriklere göre hedefin %0–150'si.
          </li>
          <li>
            <b>Rol bazında 2–3 metrik.</b> Mentor: öğrenci sonucu ve yenileme; Onboarding: aktivasyon; Support: çözüm süresi ve memnuniyet; Yazılım: teslim
            ve hata; Pazarlama: lead ve maliyet. Satış komisyonu ayrı kalır (2026'da aylık {usd(h.quarters.reduce((a, q) => a + q.satis_prim_usd, 0) / months)}).
          </li>
          <li>
            <b>Çeyreklik ödeme (Q1–Q4), maaştan ayrı kalem.</b> Maaşla aynı ödemede gelince zam sanılıyor; ayrı ve hedefe bağlı ödenince prim olarak
            algılanır.
          </li>
          <li>
            <b>C-Level ekip havuzundan çıkar</b>, kâr payına bağlanır. Havuz dışında kendi performans primi alanlar da yeni sistemin metriklerine
            taşınır; iki ayrı prim düzeni kalmaz.
          </li>
          <li>
            <b>Donation</b> primden bağımsız kalır (kârın %6'sı); prim bütçesinden düşülmez.
          </li>
        </ol>
      </div>
    </section>
  );
}

// --- kişiye özel karar ----------------------------------------------------------------

function PersonPlanForm({ person, onClose, onSave }: { person: CompassPerson; onClose: () => void; onSave: (p: CompassPersonPlan) => Promise<void> }) {
  const [pctIn, setPct] = useState(person.plan.pct === null ? "" : String(person.plan.pct * 100));
  const [adj, setAdj] = useState(person.plan.adj ? String(person.plan.adj * 100) : "");
  const [note, setNote] = useState(person.plan.note);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const num = (s: string) => Number(s.replace(",", ".")) / 100;

  async function save() {
    setBusy(true);
    setErr(null);
    try {
      await onSave({ pct: pctIn.trim() === "" ? null : num(pctIn), adj: adj.trim() === "" ? 0 : num(adj), note: note.trim() });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
      setBusy(false);
    }
  }

  return (
    <Modal title={`${person.person}: zam kararı`} onClose={onClose}>
      <p className="muted">Kural önerisi {pct(person.rate)}. Boş bırakılan alan kurala döner.</p>
      <div className="f-grid">
        <Field label="Sabit artış (%)" hint="ör. 0 = zam yok; kuralı tamamen geçersiz kılar">
          <input className="f-input num" inputMode="decimal" value={pctIn} onChange={(e) => setPct(e.target.value)} />
        </Field>
        <Field label="Ek düzeltme (%)" hint="eşitlik / telafi; kural artışının üstüne">
          <input className="f-input num" inputMode="decimal" value={adj} onChange={(e) => setAdj(e.target.value)} />
        </Field>
      </div>
      <Field label="Gerekçe">
        <input className="f-input" value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
      </Field>
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
