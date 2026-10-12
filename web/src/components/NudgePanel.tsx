import { useEffect, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { clock } from "../format";
import type { NudgeItem, NudgeQueue, NudgeStatus } from "../types";

const STATUS_LABEL: Record<NudgeStatus, string> = {
  bekliyor: "Onay bekliyor",
  gonderiliyor: "Gönderiliyor",
  gonderildi: "Gönderildi",
  reddedildi: "Reddedildi",
  suresi_doldu: "Süresi doldu",
  hata: "Gönderilemedi",
};

/** Tempo dürtmeleri: onay bekleyenler üstte, bugün gidenler altta. */
export function NudgePanel() {
  const { data, refresh } = usePolling<NudgeQueue>("/api/nudges", 60_000);
  if (!data) return null;
  const pending = data.items.filter((n) => n.status === "bekliyor");
  const done = data.items.filter((n) => n.status !== "bekliyor");

  return (
    <section className="card">
      <div className="card-head">
        <h2>Dürtmeler{pending.length ? ` · ${pending.length} onay bekliyor` : ""}</h2>
        <span className="hint">
          {data.approval_required
            ? "ilk 14 gün yapay zekâlı mesajlar senin onayınla gider · 45 dk içinde onaylanmazsa düşer"
            : "otomatik gönderim açık"}
          {data.ai ? "" : " · yapay zekâ kapalı, yalnız sayısal uyarı"}
        </span>
      </div>
      <div className="card-body nudges">
        {pending.length === 0 && done.length === 0 ? (
          <div className="empty">Bugün hedefin gerisinde kalan temsilciye henüz dürtme gitmedi.</div>
        ) : null}
        {pending.map((n) => (
          <PendingNudge key={n.id} item={n} onDone={refresh} />
        ))}
        {done.length ? (
          <details className="nudge-log">
            <summary>Bugün: {done.length} dürtme</summary>
            <ul>
              {done.map((n) => (
                <li key={n.id}>
                  <div className="nudge-meta">
                    <b>{n.rep_name}</b> · {clock(n.sent_at ?? n.created_at)} ·{" "}
                    <span className={`nudge-status ${n.status}`}>{STATUS_LABEL[n.status]}</span>
                    {n.shadow ? <span className="muted"> · gölge</span> : null}
                    {n.failure ? <span className="muted"> · {n.failure}</span> : null}
                  </div>
                  <pre className="nudge-text">{n.text}</pre>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
    </section>
  );
}

function PendingNudge({ item, onDone }: { item: NudgeItem; onDone: () => void }) {
  const [text, setText] = useState(item.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(item.text), [item.text]);

  async function decide(approve: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/nudges/${item.id}`, {
        method: "POST",
        body: JSON.stringify({ approve, text: approve ? text : null }),
      });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="nudge-pending">
      <div className="nudge-meta">
        <b>{item.rep_name}</b> · {clock(item.created_at)} · Cliq mesajı
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.min(10, text.split("\n").length + 1)}
        aria-label={`${item.rep_name} için mesaj`}
      />
      {error ? <div className="banner error">{error}</div> : null}
      <div className="nudge-actions">
        <button className="btn primary" onClick={() => decide(true)} disabled={busy || !text.trim()}>
          Onayla ve gönder
        </button>
        <button className="btn" onClick={() => decide(false)} disabled={busy}>
          Reddet
        </button>
        {item.ai_text ? <span className="muted">💡 satırını yapay zekâ yazdı; düzenleyebilirsin.</span> : null}
      </div>
    </div>
  );
}
