import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { StatTile } from "../components/StatTile";
import { day, istanbulToday, money, monday, short, usd } from "./format";
import type { CompanyId, Meta, SnapshotCard, SnapshotLine, Snapshots } from "./types";
import { CompanyTag, Field, Loading, Modal, scopeIds, useFin, type TabProps } from "./ui";

const ORIGIN: Record<SnapshotLine["origin"], string> = { account: "hesap", planned: "planlı", manual: "elle" };

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

  return (
    <>
      <div className="controls">
        <label className="field">
          Hafta
          <select className="f-input" value={data.week ?? ""} onChange={(e) => setWeek(e.target.value || null)} disabled={!data.weeks.length}>
            {data.weeks.length ? null : <option value="">Henüz hafta yok</option>}
            {data.weeks.map((w) => (
              <option key={w} value={w}>
                {day(w)}
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

      {!data.week ? (
        <section className="card">
          <div className="card-body empty">
            Henüz haftalık bilanço yok. "Yeni hafta" hesap bakiyeleri ve açık planlı kalemlerden taslak oluşturur.
          </div>
        </section>
      ) : (
        <>
          {data.missing_fx.length ? <div className="banner">Kur kilitlenmemiş: {data.missing_fx.join(", ")}. Bu satırlar toplama girmedi.</div> : null}
          <div className="tiles fin-tiles">
            <StatTile label="Aktifler" value={short(data.totals.assets_usd)} expected={usd(data.totals.assets_usd)} />
            <StatTile label="Pasifler" value={short(data.totals.liabilities_usd)} expected={usd(data.totals.liabilities_usd)} />
            <StatTile
              label="Net"
              value={short(data.totals.net_usd)}
              expected={usd(data.totals.net_usd)}
              tone={data.totals.net_usd >= 0 ? "good" : "critical"}
              badge={data.totals.net_usd >= 0 ? "pozitif" : "negatif"}
            />
            <StatTile
              label="Kesinleşen"
              value={`${data.snapshots.filter((s) => s.locked).length}/${data.snapshots.length}`}
              expected="şirket"
            />
          </div>
          {data.snapshots.length ? (
            data.snapshots.map((s) => <SnapshotView key={s.id} snap={s} meta={meta} onChanged={refresh} />)
          ) : (
            <section className="card">
              <div className="card-body empty">Bu hafta seçili şirket için bilanço girilmemiş.</div>
            </section>
          )}
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

function SnapshotView({ snap, meta, onChanged }: { snap: SnapshotCard; meta: Meta; onChanged: () => void }) {
  const [notes, setNotes] = useState(snap.notes);
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const company = meta.companies.find((c) => c.id === snap.company_id);
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

  const sides: SnapshotLine["side"][] = ["asset", "liability"];
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          <CompanyTag id={snap.company_id} /> {company?.name}
        </h2>
        <span className="hint">
          {snap.locked ? `kesinleşti · ${day(snap.locked_at)}` : "taslak"} · {fxText || "kur yok"}
        </span>
      </div>
      <div className="card-body">
        {err ? <div className="banner error">{err}</div> : null}
        <div className="fin-bs">
          {sides.map((side) => {
            const lines = snap.lines.filter((l) => l.side === side);
            const groups = snap.groups.filter((g) => g.side === side);
            const total = side === "asset" ? snap.assets_usd : snap.liabilities_usd;
            return (
              <div key={side} className="fin-bs-side">
                <div className="fin-bs-head">
                  <b>{side === "asset" ? "Aktifler" : "Pasifler"}</b>
                  <b>{usd(total)}</b>
                </div>
                {groups.length ? null : <div className="muted">—</div>}
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
              </div>
            );
          })}
        </div>
        <div className="fin-bs-net">
          Net <b>{usd(snap.net_usd)}</b>
        </div>
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
            <button className="btn" onClick={() => setAdding(true)}>
              Satır ekle
            </button>
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
          onClose={() => setAdding(false)}
          onSave={async (body) => {
            await call(`/api/finance/snapshots/${snap.id}/lines`, "POST", body);
            setAdding(false);
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

function LineForm({ snap, onClose, onSave }: { snap: SnapshotCard; onClose: () => void; onSave: (body: Record<string, unknown>) => void }) {
  const currencies = ["USD", ...Object.keys(snap.fx)];
  const [f, setF] = useState({ side: "liability", group_name: "Tedarikçiler", label: "", amount: "", currency: currencies[0] });
  const set = (k: keyof typeof f, v: string) => setF({ ...f, [k]: v });
  const groups = Array.from(new Set(snap.lines.filter((l) => l.side === f.side).map((l) => l.group_name)));
  return (
    <Modal title="Satır ekle" onClose={onClose}>
      <div className="f-grid">
        <Field label="Taraf">
          <select className="f-input" value={f.side} onChange={(e) => set("side", e.target.value)}>
            <option value="asset">Aktif</option>
            <option value="liability">Pasif</option>
          </select>
        </Field>
        <Field label="Grup">
          <input className="f-input" list="fin-groups" value={f.group_name} onChange={(e) => set("group_name", e.target.value)} />
          <datalist id="fin-groups">
            {groups.map((g) => (
              <option key={g} value={g} />
            ))}
          </datalist>
        </Field>
        <Field label="Kalem">
          <input className="f-input" value={f.label} onChange={(e) => set("label", e.target.value)} />
        </Field>
        <Field label="Tutar">
          <input className="f-input num" type="number" step="0.01" value={f.amount} onChange={(e) => set("amount", e.target.value)} />
        </Field>
        <Field label="Para birimi" hint="yalnız bu haftaya kilitlenen kurlar">
          <select className="f-input" value={f.currency} onChange={(e) => set("currency", e.target.value)}>
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
          disabled={!f.label.trim() || !f.group_name.trim() || f.amount === ""}
          onClick={() => onSave({ ...f, label: f.label.trim(), group_name: f.group_name.trim(), sort: 999 })}
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
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function create() {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/finance/snapshots", {
        method: "POST",
        body: JSON.stringify({ week_date: monday(week), company_ids: chosen, copy_previous: copy }),
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
        <Field label="Hafta" hint={`pazartesi: ${day(monday(week))}`}>
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
      <label className="check">
        <input type="checkbox" checked={copy} onChange={(e) => setCopy(e.target.checked)} />
        Önceki haftanın elle girilen pasiflerini kopyala
      </label>
      <p className="muted fin-note">Hesap bakiyeleri ve açık tek seferlik planlı kalemler otomatik gelir. Kurlar bugünkü ECB kuruyla haftaya kilitlenir.</p>
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
