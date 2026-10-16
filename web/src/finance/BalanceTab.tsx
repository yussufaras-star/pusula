import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, istanbulToday, money, monday, short, signed, usd } from "./format";
import type { CompanyId, Meta, SectionKey, Sheet, SheetBrief, SnapshotCard, SnapshotLine, Snapshots } from "./types";
import { CompanyTag, Field, Loading, Modal, scopeIds, useFin, type TabProps } from "./ui";

const ORIGIN: Record<SnapshotLine["origin"], string> = {
  account: "hesap",
  planned: "planlı",
  intercompany: "şirketler arası",
  manual: "elle",
};
const ASSET_SECTIONS: SectionKey[] = ["current_assets", "noncurrent_assets"];
const PASSIVE_SECTIONS: SectionKey[] = ["current_liabilities", "longterm_liabilities", "equity"];
type Catalog = Snapshots["catalog"];

const nf2 = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const ratio = (v: number | null) => (v === null ? "—" : nf2.format(v));
const pct = (v: number | null) => (v === null ? "—" : `%${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 }).format(v * 100)}`);

function delta(now: number, prev: SheetBrief | null, key: keyof Omit<SheetBrief, "week">): string | undefined {
  if (!prev) return undefined;
  return `önceki hafta ${signed(now - prev[key])}`;
}

export function BalanceTab({ scope, meta, tick }: TabProps) {
  const [week, setWeek] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [local, setLocal] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const refresh = () => setLocal((n) => n + 1);
  const path = `/api/finance/snapshots?company=${scope}${week ? `&week=${week}` : ""}`;
  const { data, error } = useFin<Snapshots>(path, tick + local);
  useEffect(() => setWeek(null), [scope]);

  if (!data) return <Loading error={error} />;

  async function copySummary() {
    if (!data?.week) return;
    try {
      const { text } = await api<{ text: string }>(`/api/finance/snapshots/summary?company=${scope}&week=${data.week}`);
      await navigator.clipboard.writeText(text);
      setCopied("Kopyalandı");
    } catch {
      setCopied("Kopyalanamadı");
    }
    window.setTimeout(() => setCopied(null), 2000);
  }

  // Tek şirkette o şirketin bilançosu, konsolidede elimine edilmiş toplam
  const headline: Sheet | null = data.consolidated ?? data.snapshots[0]?.sheet ?? null;
  const prev = data.consolidated ? data.previous_consolidated : (data.snapshots[0]?.previous ?? null);

  return (
    <>
      <div className="controls">
        <label className="field">
          Hafta
          <select className="f-input" value={data.week ?? ""} onChange={(e) => setWeek(e.target.value || null)} disabled={!data.weeks.length}>
            {data.weeks.length ? null : <option value="">Henüz hafta yok</option>}
            {data.weeks.map((w) => (
              <option key={w} value={w}>
                {day(w)} – {day(addDays6(w))}
              </option>
            ))}
          </select>
        </label>
        <div className="spacer" />
        {data.snapshots.length ? (
          <button className="btn" onClick={copySummary}>
            {copied ?? "Mail özetini kopyala"}
          </button>
        ) : null}
        <button className="btn primary" onClick={() => setCreating(true)}>
          Yeni hafta
        </button>
      </div>

      {!data.week || !headline ? (
        <section className="card">
          <div className="card-body empty">
            {data.week
              ? "Bu hafta seçili şirket için bilanço girilmemiş."
              : 'Henüz haftalık bilanço yok. "Yeni hafta" hesap bakiyeleri ve açık planlı kalemlerden taslak oluşturur.'}
          </div>
        </section>
      ) : (
        <>
          {data.missing_fx.length ? <div className="banner">Kur kilitlenmemiş: {data.missing_fx.join(", ")}. Bu satırlar toplama girmedi.</div> : null}
          <div className="tiles fin-tiles">
            <StatTile label="Aktif toplamı" value={short(data.totals.assets_usd)} expected={delta(data.totals.assets_usd, prev, "assets_usd") ?? usd(data.totals.assets_usd)} />
            <StatTile label="Yabancı kaynaklar" value={short(data.totals.debts_usd)} expected={delta(data.totals.debts_usd, prev, "debts_usd") ?? "kısa + uzun vadeli"} />
            <StatTile
              label="Özkaynak"
              value={short(data.totals.equity_usd)}
              expected={delta(data.totals.equity_usd, prev, "equity_usd") ?? usd(data.totals.equity_usd)}
              tone={data.totals.equity_usd >= 0 ? "good" : "critical"}
              badge={data.totals.equity_usd >= 0 ? "pozitif" : "negatif"}
            />
            <StatTile
              label="Cari oran"
              value={ratio(headline.ratios.current)}
              expected={headline.ratios.current === null ? "veri yetersiz" : "dönen varlık / KV borç"}
              tone={headline.ratios.current === null ? "neutral" : headline.ratios.current >= 1 ? "good" : "critical"}
              badge={headline.ratios.current === null ? undefined : headline.ratios.current >= 1 ? "1 üstü" : "1 altı"}
            />
          </div>

          {data.consolidated ? (
            <section className="card">
              <div className="card-head">
                <h2>Konsolide bilanço</h2>
                <span className="hint">
                  {data.snapshots.length} şirket · şirketler arası alacak ve borçlar elimine · USD, haftaya kilitli kurla
                </span>
              </div>
              <div className="card-body">
                <EliminationNote sheet={data.consolidated} />
                <SheetView sheet={data.consolidated} />
                <RatioStrip sheet={data.consolidated} />
              </div>
            </section>
          ) : null}

          {data.snapshots.map((s) => (
            <SnapshotView key={s.id} snap={s} meta={meta} catalog={data.catalog} onChanged={refresh} />
          ))}
        </>
      )}

      {creating ? (
        <NewWeek
          meta={meta}
          ids={scopeIds(scope, meta)}
          onClose={() => setCreating(false)}
          onCreated={(w) => {
            setCreating(false);
            setWeek(w);
            refresh();
          }}
        />
      ) : null}
    </>
  );
}

function addDays6(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 6);
  return d.toISOString().slice(0, 10);
}

function EliminationNote({ sheet }: { sheet: Sheet }) {
  if (!sheet.eliminated_receivables_usd && !sheet.eliminated_payables_usd) return null;
  const gap = Math.abs(sheet.elimination_gap_usd) > 1;
  return (
    <div className={`banner${gap ? "" : " fin-info"}`}>
      Elimine edilen: şirketler arası alacak {usd(sheet.eliminated_receivables_usd)}, borç {usd(sheet.eliminated_payables_usd)}.
      {gap ? ` Taraflar tutmuyor: fark ${usd(sheet.elimination_gap_usd)}. Bir şirketin kaydı eksik veya kur farkı olabilir.` : ""}
    </div>
  );
}

/** Salt okunur bilanço: aktif | pasif, bölüm ve grup toplamları. */
function SheetView({ sheet }: { sheet: Sheet }) {
  const section = (key: SectionKey) => sheet.sections.find((s) => s.key === key)!;
  const column = (keys: SectionKey[], title: string, total: number, extra?: React.ReactNode) => (
    <div className="fin-bs-side">
      <div className="fin-bs-head">
        <b>{title}</b>
      </div>
      {keys.map((k) => {
        const sec = section(k);
        const groups = sec.groups.filter((g) => g.usd !== 0);
        return (
          <div key={k} className="fin-bs-section">
            <div className="fin-bs-sname">
              <span>{sec.title}</span>
              <span>{usd(sec.usd)}</span>
            </div>
            {groups.map((g) => (
              <div key={g.name} className="fin-bs-line">
                <span>{g.name}</span>
                <span className="num">{usd(g.usd)}</span>
              </div>
            ))}
            {k === "equity" ? extra : null}
          </div>
        );
      })}
      <div className="fin-bs-total">
        <span>{title} toplamı</span>
        <b>{usd(total)}</b>
      </div>
    </div>
  );
  return (
    <div className="fin-bs">
      {column(ASSET_SECTIONS, "Aktif", sheet.assets_usd)}
      {column(PASSIVE_SECTIONS, "Pasif", sheet.liabilities_and_equity_usd, <EquityRows sheet={sheet} />)}
    </div>
  );
}

/** Özkaynağı girilmemiş şirketlerde hesaplanan özkaynak; bilanço tutmuyorsa denklik farkı. */
function EquityRows({ sheet }: { sheet: Sheet }) {
  const gap = Math.abs(sheet.elimination_gap_usd) > 1;
  return (
    <>
      {sheet.computed_equity_usd !== 0 || !sheet.has_equity ? (
        <div
          className="fin-bs-line fin-bs-computed"
          title="Özkaynak satırı girilmemiş şirket(ler) için bilanço denkliğinden: aktif − yabancı kaynak"
        >
          <span>{sheet.has_equity ? "Hesaplanan özkaynak (girilmemiş şirketler)" : "Özkaynak (aktif − yabancı kaynak)"}</span>
          <span className="num">{usd(sheet.computed_equity_usd)}</span>
        </div>
      ) : null}
      {sheet.balanced ? null : (
        <div className="fin-bs-line fin-bs-gap">
          <span>Denklik farkı ({gap ? "şirketler arası kayıtlar tutmuyor veya eksik kalem" : "eksik kalem olabilir"})</span>
          <span className="num">{usd(sheet.difference_usd)}</span>
        </div>
      )}
    </>
  );
}

function RatioStrip({ sheet }: { sheet: Sheet }) {
  const r = sheet.ratios;
  const items: [string, string, string][] = [
    ["Cari oran", ratio(r.current), "dönen varlık / KV yabancı kaynak"],
    ["Asit-test", ratio(r.acid_test), "(dönen − stok) / KV yabancı kaynak"],
    ["Nakit oranı", ratio(r.cash), "hazır değerler / KV yabancı kaynak"],
    ["Net işletme sermayesi", usd(r.working_capital_usd), "dönen varlık − KV yabancı kaynak"],
    ["Borç / özkaynak", ratio(r.debt_to_equity), "yabancı kaynak / özkaynak"],
    ["Özkaynak oranı", pct(r.equity_ratio), "özkaynak / aktif"],
  ];
  return (
    <div className="fin-ratios">
      {items.map(([label, value, hint]) => (
        <div key={label} className="fin-ratio" title={hint}>
          <span className="muted">{label}</span>
          <b>{value === "—" ? <span className="muted">veri yetersiz</span> : value}</b>
        </div>
      ))}
    </div>
  );
}

function SnapshotView({ snap, meta, catalog, onChanged }: { snap: SnapshotCard; meta: Meta; catalog: Catalog; onChanged: () => void }) {
  const [notes, setNotes] = useState(snap.notes);
  const [adding, setAdding] = useState<SectionKey | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const company = meta.companies.find((c) => c.id === snap.company_id);
  const sheet = snap.sheet;
  const fxText = Object.entries(snap.fx)
    .map(([c, r]) => `1 ${c} = ${r.toFixed(c === "TRY" ? 5 : 4)} USD`)
    .join(" · ");

  async function call(path: string, method: string, body?: unknown) {
    setErr(null);
    try {
      await api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Kaydedilemedi");
    }
  }

  const column = (keys: SectionKey[], title: string, total: number) => (
    <div className="fin-bs-side">
      <div className="fin-bs-head">
        <b>{title}</b>
      </div>
      {keys.map((k) => {
        const sec = sheet.sections.find((s) => s.key === k)!;
        const lines = snap.lines.filter((l) => l.section === k);
        const groups = sec.groups.filter((g) => g.usd !== 0 || lines.some((l) => l.group_name === g.name));
        return (
          <div key={k} className="fin-bs-section">
            <div className="fin-bs-sname">
              <span>{sec.title}</span>
              <span>{usd(sec.usd)}</span>
            </div>
            {groups.map((g) => (
              <div key={g.name} className="fin-bs-group">
                <div className="fin-bs-gname">
                  <span>{g.name}</span>
                  <span>{usd(g.usd)}</span>
                </div>
                {lines
                  .filter((l) => l.group_name === g.name)
                  .map((l) => (
                    <LineRow
                      key={l.id}
                      line={l}
                      locked={snap.locked}
                      onSave={(amount) => call(`/api/finance/snapshot-lines/${l.id}`, "PATCH", { amount })}
                      onDelete={() => call(`/api/finance/snapshot-lines/${l.id}`, "DELETE")}
                    />
                  ))}
              </div>
            ))}
            {k === "equity" ? <EquityRows sheet={sheet} /> : null}
            {snap.locked ? null : (
              <button className="btn sm link-add" onClick={() => setAdding(k)}>
                + {sec.title.toLocaleLowerCase("tr")} kalemi
              </button>
            )}
          </div>
        );
      })}
      <div className="fin-bs-total">
        <span>{title} toplamı</span>
        <b>{usd(total)}</b>
      </div>
    </div>
  );

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          <CompanyTag id={snap.company_id} /> {company?.name}
        </h2>
        <span className="hint">
          {snap.locked ? `kesinleşti · ${day(snap.locked_at)}` : "taslak"} · {fxText || "kur yok"}
          {snap.previous ? ` · önceki hafta özkaynak ${usd(snap.previous.equity_usd)} (${signed(sheet.equity_usd - snap.previous.equity_usd)})` : ""}
        </span>
      </div>
      <div className="card-body">
        {err ? <div className="banner error">{err}</div> : null}
        {sheet.missing_fx.length ? <div className="banner">Kur kilitlenmemiş: {sheet.missing_fx.join(", ")}.</div> : null}
        <div className="fin-bs">
          {column(ASSET_SECTIONS, "Aktif", sheet.assets_usd)}
          {column(PASSIVE_SECTIONS, "Pasif", sheet.liabilities_and_equity_usd)}
        </div>
        <RatioStrip sheet={sheet} />
        <Field label="Not">
          <textarea
            className="f-input"
            rows={2}
            value={notes}
            disabled={snap.locked}
            onChange={(e) => setNotes(e.target.value)}
            onBlur={() => notes !== snap.notes && call(`/api/finance/snapshots/${snap.id}`, "PATCH", { notes })}
          />
        </Field>
        {snap.locked ? null : (
          <div className="f-actions">
            <button
              className="btn danger"
              onClick={() => window.confirm("Bu şirketin haftası silinsin mi?") && call(`/api/finance/snapshots/${snap.id}`, "DELETE")}
            >
              Haftayı sil
            </button>
            <div className="spacer" />
            {!sheet.balanced ? <span className="warn-text">Bilanço denk değil; kesinleştirmeden önce farkı kapat.</span> : null}
            <button
              className="btn primary"
              onClick={() =>
                window.confirm("Kesinleştirilsin mi? Sonra satırlar ve kurlar değiştirilemez.") && call(`/api/finance/snapshots/${snap.id}/lock`, "POST")
              }
            >
              Kesinleştir
            </button>
          </div>
        )}
      </div>
      {adding ? (
        <LineForm
          snap={snap}
          catalog={catalog}
          section={adding}
          onClose={() => setAdding(null)}
          onSave={async (body) => {
            await call(`/api/finance/snapshots/${snap.id}/lines`, "POST", body);
            setAdding(null);
          }}
        />
      ) : null}
    </section>
  );
}

function LineRow({ line, locked, onSave, onDelete }: { line: SnapshotLine; locked: boolean; onSave: (amount: string) => void; onDelete: () => void }) {
  const [value, setValue] = useState(String(line.amount));
  useEffect(() => setValue(String(line.amount)), [line.amount]);
  return (
    <div className="fin-bs-line">
      <span className="fin-desc">
        {line.label} <span className="muted">· {ORIGIN[line.origin]}</span>
      </span>
      {locked ? (
        <span className="num">{money(line.amount, line.currency, 2)}</span>
      ) : (
        <span className="fin-bs-edit">
          <input
            className="f-input sm num"
            type="number"
            step="0.01"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onBlur={() => value !== String(line.amount) && value !== "" && onSave(value)}
            aria-label={`${line.label} tutarı`}
          />
          <span className="muted">{line.currency}</span>
          <button className="btn sm link-x" onClick={onDelete} aria-label="Satırı sil" title="Sil">
            ×
          </button>
        </span>
      )}
    </div>
  );
}

const OTHER = "__diger__";

function LineForm({
  snap,
  catalog,
  section,
  onClose,
  onSave,
}: {
  snap: SnapshotCard;
  catalog: Catalog;
  section: SectionKey;
  onClose: () => void;
  onSave: (body: Record<string, unknown>) => void;
}) {
  const currencies = ["USD", ...Object.keys(snap.fx)];
  const [sec, setSec] = useState<SectionKey>(section);
  const groups = catalog.find((c) => c.key === sec)?.groups ?? [];
  const [group, setGroup] = useState(groups[0] ?? OTHER);
  const [custom, setCustom] = useState("");
  const [label, setLabel] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(currencies[0]);
  const groupName = group === OTHER ? custom.trim() : group;

  return (
    <Modal title="Bilanço kalemi ekle" onClose={onClose}>
      <div className="f-grid">
        <Field label="Bölüm">
          <select
            className="f-input"
            value={sec}
            onChange={(e) => {
              const next = e.target.value as SectionKey;
              setSec(next);
              setGroup(catalog.find((c) => c.key === next)?.groups[0] ?? OTHER);
            }}
          >
            {catalog.map((c) => (
              <option key={c.key} value={c.key}>
                {c.side === "asset" ? "Aktif" : "Pasif"} · {c.title}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Grup">
          <select className="f-input" value={group} onChange={(e) => setGroup(e.target.value)}>
            {groups.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
            <option value={OTHER}>Diğer (yaz)</option>
          </select>
        </Field>
        {group === OTHER ? (
          <Field label="Grup adı">
            <input className="f-input" value={custom} onChange={(e) => setCustom(e.target.value)} />
          </Field>
        ) : null}
        <Field label="Kalem">
          <input className="f-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="ör. Ambalaj stoğu, Banka kredisi, Sermaye" />
        </Field>
        <Field label="Tutar" hint="borç ve özkaynak da pozitif yazılır">
          <input className="f-input num" type="number" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        <Field label="Para birimi" hint="yalnız bu haftaya kilitlenen kurlar">
          <select className="f-input" value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {currencies.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
      </div>
      <div className="f-actions">
        <div className="spacer" />
        <button className="btn" onClick={onClose}>
          Vazgeç
        </button>
        <button
          className="btn primary"
          disabled={!label.trim() || !groupName || amount === ""}
          onClick={() => onSave({ section: sec, group_name: groupName, label: label.trim(), amount, currency, sort: 999 })}
        >
          Ekle
        </button>
      </div>
    </Modal>
  );
}

function NewWeek({ meta, ids, onClose, onCreated }: { meta: Meta; ids: CompanyId[]; onClose: () => void; onCreated: (week: string) => void }) {
  const [week, setWeek] = useState(monday(istanbulToday()));
  const [chosen, setChosen] = useState<CompanyId[]>(ids);
  const [copy, setCopy] = useState(true);
  const [ic, setIc] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/finance/snapshots", {
        method: "POST",
        body: JSON.stringify({ week_date: monday(week), company_ids: chosen, copy_previous: copy, include_intercompany: ic }),
      });
      onCreated(monday(week));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Oluşturulamadı");
      setBusy(false);
    }
  }

  return (
    <Modal title="Yeni haftalık bilanço" onClose={onClose}>
      <div className="f-grid">
        <Field label="Hafta" hint={`${day(monday(week))} – ${day(addDays6(monday(week)))} · bilanço tarihi pazar`}>
          <input className="f-input" type="date" value={week} onChange={(e) => e.target.value && setWeek(e.target.value)} />
        </Field>
        <Field label="Şirketler">
          <div className="chips">
            {meta.companies.map((c) => {
              const on = chosen.includes(c.id);
              return (
                <button key={c.id} className={`chip${on ? " on" : ""}`} aria-pressed={on} onClick={() => setChosen(on ? chosen.filter((x) => x !== c.id) : [...chosen, c.id])}>
                  {c.name}
                </button>
              );
            })}
          </div>
        </Field>
      </div>
      <div className="fin-checks fin-checks-col">
        <label className="check">
          <input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} />
          Önceki haftanın elle girilen kalemlerini kopyala (stok, demirbaş, kredi, sermaye vb.)
        </label>
        <label className="check">
          <input type="checkbox" checked={ic} onChange={(e) => setIc(e.target.checked)} />
          Şirketler arası net transferleri alacak / borç olarak ekle (hareketlerden, USD)
        </label>
      </div>
      <p className="muted fin-note">
        Hesap bakiyeleri hazır değerlere, negatif bakiyeler mali borçlara, açık tek seferlik planlı kalemler ticari alacak ve borçlara
        yazılır; vadesi bir yıldan uzak olanlar uzun vadelidir. Kurlar bugünkü ECB kuruyla haftaya kilitlenir.
      </p>
      {err ? <div className="banner error">{err}</div> : null}
      <div className="f-actions">
        <div className="spacer" />
        <button className="btn" onClick={onClose}>
          Vazgeç
        </button>
        <button className="btn primary" onClick={create} disabled={busy || !chosen.length}>
          {busy ? "Oluşturuluyor…" : "Oluştur"}
        </button>
      </div>
    </Modal>
  );
}
