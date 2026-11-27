import { useState, type ReactNode } from "react";

import { api, ApiError, usePolling } from "../api";
import { CeoSummaryPage } from "../pages/CeoSummary";
import { AccountsTab } from "./AccountsTab";
import { BalanceTab } from "./BalanceTab";
import { DecisionsTab } from "./DecisionsTab";
import { FlowsTab } from "./FlowsTab";
import { addDays, day, istanbulToday, SCOPES } from "./format";
import { IntercompanyTab } from "./IntercompanyTab";
import { OverviewTab } from "./OverviewTab";
import { CompassTab } from "./CompassTab";
import { ProjectionTab } from "./ProjectionTab";
import { RecurringTab } from "./RecurringTab";
import { TaxTab } from "./TaxTab";
import { AlertsTab } from "./AlertsTab";
import type { Meta, Scope, SyncReport } from "./types";
import { Loading, type TabProps } from "./ui";

// Sekmeler finans işinin ritmine göre: her gün Genel ve Uyarılar, her hafta Ödeme planı,
// her ay Nakit akışı, Bilanço ve Vergi. Kararlar ve Cliq kartı Genel'de, düzenli ödemeler
// Ödeme planı'nda (projeksiyonun altında), şirketler arası transferler Nakit akışı'nda.
export type FinanceTab = "genel" | "akis" | "projeksiyon" | "personel" | "bilanco" | "vergi" | "uyarilar" | "hesaplar";

const TITLES: Record<FinanceTab, [string, string]> = {
  genel: ["Genel bakış", "Konsolide nakit, şirketler, uyarılar, kararlar ve Cliq'e giden özet"],
  akis: ["Nakit akışı", "Aylık giriş ve çıkış, kategoriler, hareketler ve şirketler arası transferler"],
  personel: ["Maaş ve prim pusulası", "2027 zam önerisi ve gerekçesi, kıdem, prim ve donation geçmişi"],
  projeksiyon: ["Ödeme planı", "Planlı alacak ve borçlarla kasa seyri; geçmişte bulunan düzenli ödemeler ve abonelikler"],
  bilanco: ["Haftalık bilanço", "Şirket başına aktif ve pasif"],
  vergi: ["Cüzdan ve vergi", "LLC: satıcı ve üretici cüzdanları, yılın kârı ve yapıya göre ABD vergisi"],
  uyarilar: ["Uyarılar", "Yeni abonelik, olağan dışı transfer ve Rexven'e işlenmemiş üretici ödemesi"],
  hesaplar: ["Hesaplar", "Banka bağlantıları ve ekstre yükleme"],
};

// Tarih aralığı yalnız geçmiş veriye bakan bölümleri etkiler (Ödeme planı'nda düzenli ödemeler).
const RANGED: FinanceTab[] = ["genel", "akis", "projeksiyon", "vergi"];

type Preset = "3" | "6" | "12" | "yil" | "ozel";
const PRESETS: { key: Preset; label: string }[] = [
  { key: "3", label: "3 ay" },
  { key: "6", label: "6 ay" },
  { key: "12", label: "12 ay" },
  { key: "yil", label: "Bu yıl" },
];

function presetRange(p: Preset, today: string): [string, string] {
  if (p === "yil") return [`${today.slice(0, 4)}-01-01`, today];
  const months = Number(p) || 6;
  return [addDays(today, -Math.round(months * 30.4)), today];
}

// Seçimler tarayıcıda hatırlanır (yalnız kolaylık; erişilemezse varsayılan).
function remember<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const v = window.localStorage.getItem(`fin.${key}`);
    return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function store(key: string, value: string) {
  try {
    window.localStorage.setItem(`fin.${key}`, value);
  } catch {
    /* gizli pencere vb. */
  }
}

export function FinancePage({ tab }: { tab: FinanceTab }) {
  const today = istanbulToday();
  const [scope, setScope] = useState<Scope>(() => remember<Scope>("scope", "all", ["all", "llc", "gmbh", "tr"]));
  const [preset, setPreset] = useState<Preset>(() => remember<Preset>("preset", "6", ["3", "6", "12", "yil"]));
  const [custom, setCustom] = useState<[string, string]>(() => presetRange("6", today));
  const [start, end] = preset === "ozel" ? custom : presetRange(preset, today);
  const [tick, setTick] = useState(0);
  const reload = () => setTick((n) => n + 1);
  const meta = usePolling<Meta>("/api/finance/meta", 0);
  const [title, sub] = TITLES[tab];
  const ranged = RANGED.includes(tab);

  const pickScope = (s: Scope) => {
    setScope(s);
    store("scope", s);
  };
  const pickPreset = (p: Preset) => {
    setPreset(p);
    if (p !== "ozel") store("preset", p);
  };

  const props: Omit<TabProps, "meta"> = { scope, start, end, tick, reload };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <div className="sub">
            {sub}
            {meta.data?.fx_date ? ` · kur ${day(meta.data.fx_date)} (ECB)` : ""}
          </div>
        </div>
        <div className="controls">
          <div className="seg" role="tablist" aria-label="Şirket">
            {SCOPES.map((s) => (
              <button key={s.key} role="tab" aria-selected={scope === s.key} onClick={() => pickScope(s.key)}>
                {s.label}
              </button>
            ))}
          </div>
          <FinanceSync onDone={() => {
            meta.refresh();
            reload();
          }} />
        </div>
      </div>

      {ranged ? (
        <div className="controls fin-range">
          <div className="chips" role="group" aria-label="Dönem">
            {PRESETS.map((p) => (
              <button key={p.key} className={`chip${preset === p.key ? " on" : ""}`} aria-pressed={preset === p.key} onClick={() => pickPreset(p.key)}>
                {p.label}
              </button>
            ))}
            <button className={`chip${preset === "ozel" ? " on" : ""}`} aria-pressed={preset === "ozel"} onClick={() => {
              setCustom([start, end]);
              pickPreset("ozel");
            }}>
              Özel
            </button>
          </div>
          {preset === "ozel" ? (
            <>
              <input type="date" value={custom[0]} max={custom[1]} onChange={(e) => e.target.value && setCustom([e.target.value, custom[1]])} aria-label="Başlangıç" />
              <span className="muted">–</span>
              <input type="date" value={custom[1]} min={custom[0]} onChange={(e) => e.target.value && setCustom([custom[0], e.target.value])} aria-label="Bitiş" />
            </>
          ) : null}
        </div>
      ) : null}

      {!meta.data ? (
        <Loading error={meta.error} />
      ) : tab === "genel" ? (
        <>
          <OverviewTab {...props} meta={meta.data} />
          <Part title="Kararlar">
            <DecisionsTab {...props} meta={meta.data} />
          </Part>
          <CeoSummaryPage
            embedded
            endpoint="/api/finance-summary"
            title="Cliq'e giden yönetici özeti"
            sub="Her sabah son iş gününden beri nakit, giriş-çıkış ve uyarılar #headquarters kanalına gider · tutarlar USD"
          />
        </>
      ) : tab === "akis" ? (
        <>
          <FlowsTab {...props} meta={meta.data} />
          <Part title="Şirketler arası">
            <IntercompanyTab {...props} meta={meta.data} />
          </Part>
        </>
      ) : tab === "personel" ? (
        <CompassTab {...props} meta={meta.data} />
      ) : tab === "projeksiyon" ? (
        <>
          <ProjectionTab {...props} meta={meta.data} />
          <Part title="Düzenli ödemeler" sub="Geçmiş hareketlerden bulunur · seçili dönem">
            <RecurringTab {...props} meta={meta.data} />
          </Part>
        </>
      ) : tab === "bilanco" ? (
        <BalanceTab {...props} meta={meta.data} />
      ) : tab === "vergi" ? (
        <TaxTab {...props} meta={meta.data} />
      ) : tab === "uyarilar" ? (
        <AlertsTab {...props} meta={meta.data} />
      ) : (
        <AccountsTab {...props} meta={meta.data} />
      )}
    </>
  );
}

/** Birleşik sekmede sonraki bölümün başlığı. */
function Part({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <>
      <div className="page-head fin-part-head">
        <div>
          <h2>{title}</h2>
          {sub ? <div className="sub">{sub}</div> : null}
        </div>
      </div>
      {children}
    </>
  );
}

/** Kurlar + API hesapları. Dakikada en fazla bir kez (sunucu da sınırlar). */
function FinanceSync({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);

  async function run() {
    setBusy(true);
    setNote(null);
    try {
      const r = await api<SyncReport>("/api/finance/sync", { method: "POST", body: "{}" });
      if (r.skipped === "yeni_guncellendi") setNote({ text: "Az önce güncellendi", ok: true });
      else if (r.skipped === "baska_calisma_suruyor") setNote({ text: "Güncelleme zaten sürüyor", ok: false });
      else {
        const accounts = r.accounts ?? [];
        const failed = accounts.filter((a) => a.error).map((a) => a.name);
        const left = accounts.filter((a) => a.skipped).length;
        const added = accounts.reduce((s, a) => s + (a.inserted ?? 0), 0);
        if (failed.length) setNote({ text: `Hata: ${failed.join(", ")}`, ok: false });
        else if (r.fx && !r.fx.ok) setNote({ text: "Kurlar alınamadı", ok: false });
        else setNote({ text: `${added} yeni hareket${left ? ` · ${left} hesap sonraki turda` : ""}`, ok: true });
      }
      onDone();
    } catch (err) {
      setNote({ text: err instanceof ApiError ? err.message : "Bağlantı hatası", ok: false });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {note ? (
        <span className={`badge ${note.ok ? "good" : "warning"}`} role="status">
          {note.text}
        </span>
      ) : null}
      <button className="btn primary" onClick={run} disabled={busy} title="Kurları ve API'li hesapları şimdi güncelle">
        {busy ? "Güncelleniyor…" : "Hesapları güncelle"}
      </button>
    </>
  );
}
