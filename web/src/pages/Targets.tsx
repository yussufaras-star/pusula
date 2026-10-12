import { useEffect, useMemo, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { num, pct } from "../format";
import type { TargetMetric, TargetsPayload, TargetValues } from "../types";

const ORDER: TargetMetric[] = ["arama", "ulasilan", "randevu", "doluluk"];
const DEFAULT_ID = "*";

type Draft = Record<string, Record<TargetMetric, string>>;

function toDraft(values: TargetValues): Record<TargetMetric, string> {
  return Object.fromEntries(ORDER.map((m) => [m, values[m] === null ? "" : String(values[m])])) as Record<
    TargetMetric,
    string
  >;
}

function avgLabel(metric: TargetMetric, v: number | null): string {
  if (v === null) return "veri yok";
  return metric === "doluluk" ? `ort. ${pct(v)}` : `ort. ${num(v)}`;
}

export function TargetsPage() {
  const { data, error, refresh } = usePolling<TargetsPayload>("/api/targets", 0);
  const [draft, setDraft] = useState<Draft>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const initial = useMemo<Draft>(() => {
    if (!data) return {};
    const out: Draft = { [DEFAULT_ID]: toDraft(data.default.values) };
    for (const r of data.reps) out[r.rep_id] = toDraft(r.values);
    return out;
  }, [data]);

  useEffect(() => setDraft(initial), [initial]);

  const changes = useMemo(() => {
    const rows: { rep_id: string; metric: TargetMetric; value: number | null }[] = [];
    for (const [repId, values] of Object.entries(draft)) {
      for (const m of ORDER) {
        if ((values[m] ?? "") === (initial[repId]?.[m] ?? "")) continue;
        const raw = values[m].trim().replace(",", ".");
        rows.push({ rep_id: repId, metric: m, value: raw === "" ? null : Number(raw) });
      }
    }
    return rows;
  }, [draft, initial]);

  const invalid = changes.some((c) => c.value !== null && (!Number.isFinite(c.value) || c.value < 0));

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      await api("/api/targets", { method: "PUT", body: JSON.stringify({ rows: changes }) });
      setMessage({ ok: true, text: "Hedefler kaydedildi; canlı panel ve dürtmeler bunları kullanır." });
      refresh();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setSaving(false);
    }
  }

  function set(repId: string, metric: TargetMetric, value: string) {
    setDraft((d) => ({ ...d, [repId]: { ...d[repId], [metric]: value } }));
  }

  function row(repId: string, name: string, average: TargetValues, isDefault = false) {
    return (
      <tr key={repId} className={isDefault ? "target-default" : undefined}>
        <td className="name">
          {name}
          {isDefault ? <div className="sub">boş bırakılan temsilciye uygulanır</div> : null}
        </td>
        {ORDER.map((m) => (
          <td key={m} className="num">
            <input
              className="target-input"
              inputMode="decimal"
              value={draft[repId]?.[m] ?? ""}
              placeholder={avgLabel(m, average[m])}
              onChange={(e) => set(repId, m, e.target.value)}
              aria-label={`${name} ${data?.metrics[m]} hedefi`}
            />
          </td>
        ))}
      </tr>
    );
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Günlük hedefler</h1>
          <div className="sub">
            Hafta içi tam gün için girilir; cumartesi mesai süresi oranında (6/9) uygulanır. Boş hücre: önce ekip
            varsayılanı, o da boşsa temsilcinin son 90 günlük ortalaması.
          </div>
        </div>
        <div className="controls">
          <button className="btn primary" onClick={save} disabled={saving || !changes.length || invalid}>
            {saving ? "Kaydediliyor…" : changes.length ? `Kaydet (${changes.length})` : "Kaydet"}
          </button>
        </div>
      </div>

      {error ? <div className="banner error">Hedefler alınamadı: {error.message}</div> : null}
      {message ? <div className={`banner${message.ok ? " ok" : " error"}`}>{message.text}</div> : null}
      {invalid ? <div className="banner error">Hedefler sıfır veya pozitif sayı olmalı.</div> : null}

      <section className="card">
        <div className="card-head">
          <h2>Temsilci hedefleri</h2>
          <span className="hint">gri yazı: geçmiş ortalama · doluluk yüzde olarak</span>
        </div>
        <div className="card-body">
          {!data ? (
            <div className="skeleton" />
          ) : (
            <div className="table-wrap">
              <table className="board targets">
                <thead>
                  <tr>
                    <th>Temsilci</th>
                    {ORDER.map((m) => (
                      <th key={m} className="num">
                        {data.metrics[m]}
                        {m === "doluluk" ? " %" : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {row(DEFAULT_ID, "Ekip varsayılanı", data.default.average, true)}
                  {data.reps.map((r) => row(r.rep_id, r.name, r.average))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>

      <section className="card card-body muted target-rules">
        <b className="ink-2">Dürtme kuralı.</b> Saatlik veri güncellemesinden sonra, 10:00'dan mesai bitimine 30 dk kalana
        kadar: bir başlıkta "bu saate kadar" beklenenin %80'inin altında kalan temsilciye Cliq'ten mesaj gider (doluluk
        için hedefin 10 puan altı). Aynı kişiye en sık 2 saatte bir, günde en fazla 3 mesaj. Beklenen değer temsilcinin
        kendi saatlik temposuna göre bölünür; sabah az arayan birinden 10:00'da günlük hedefin dokuzda biri beklenmez.
      </section>
    </>
  );
}
