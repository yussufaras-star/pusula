import { useState } from "react";

import { api, ApiError } from "../api";

interface StepReport {
  inserted?: number;
  written?: number;
  error?: string;
  skipped?: string;
  [module: string]: unknown;
}

interface IngestReport {
  skipped?: string;
  seconds?: number;
  steps?: Record<string, StepReport | Record<string, StepReport>>;
}

/** Zoho'dan yeni kayıt sayısı; CRM işlemleri modül bazında iç içe gelir. */
function summarize(report: IngestReport): { added: number; failed: string[] } {
  let added = 0;
  const failed: string[] = [];
  for (const [name, step] of Object.entries(report.steps ?? {})) {
    if (!step || typeof step !== "object") continue;
    if ("error" in step && step.error) {
      failed.push(name);
      continue;
    }
    if (typeof step.inserted === "number") added += step.inserted;
    else if (typeof step.written === "number") added += step.written;
    else {
      for (const sub of Object.values(step)) {
        if (sub && typeof sub === "object" && typeof (sub as StepReport).written === "number") {
          added += (sub as StepReport).written ?? 0;
        }
      }
    }
  }
  return { added, failed };
}

const STEP_LABEL: Record<string, string> = {
  aramalar: "aramalar",
  whatsapp: "WhatsApp",
  crm_islemleri: "CRM işlemleri",
  satislar: "satışlar",
  randevular: "randevular",
  leadler: "lead'ler",
  kisiler: "kişiler",
};

export function SyncButton({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "ok" | "warn" } | null>(null);

  async function run() {
    setBusy(true);
    setNote(null);
    try {
      const report = await api<IngestReport>("/api/ingest", { method: "POST" });
      if (report.skipped === "yeni_guncellendi") {
        setNote({ text: "Veri az önce güncellendi", tone: "ok" });
      } else if (report.skipped === "baska_calisma_suruyor") {
        setNote({ text: "Güncelleme zaten sürüyor", tone: "warn" });
      } else {
        const { added, failed } = summarize(report);
        const secs = report.seconds ? ` · ${Math.round(report.seconds)} sn` : "";
        setNote(
          failed.length
            ? { text: `Eksik: ${failed.map((f) => STEP_LABEL[f] ?? f).join(", ")}`, tone: "warn" }
            : { text: `${added} yeni kayıt${secs}`, tone: "ok" },
        );
      }
      onDone();
    } catch (err) {
      const timedOut = err instanceof ApiError && (err.status === 504 || err.status === 502);
      setNote({
        text: timedOut
          ? "Süre doldu; çekilen kayıtlar yine de kaydedildi"
          : err instanceof ApiError
            ? err.message
            : "Bağlantı hatası",
        tone: "warn",
      });
      onDone();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {note ? (
        <span className={`badge ${note.tone === "ok" ? "good" : "warning"}`} role="status">
          {note.text}
        </span>
      ) : null}
      <button className="btn primary" onClick={run} disabled={busy} title="Zoho'dan son kayıtları şimdi çek">
        {busy ? "Zoho'dan çekiliyor…" : "Verileri güncelle"}
      </button>
    </>
  );
}
