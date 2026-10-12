import { useEffect, useState } from "react";

import { api, ApiError } from "../api";
import { clock } from "../format";

interface Result {
  text: string;
  data_as_of: string | null;
  at: string;
}

/** Yöneticiye istek üzerine kısa AI yorumu. Aynı veri damgası için sunucu önbelleği. */
export function Commentary({ day, dataAsOf }: { day: string; dataAsOf: string | null }) {
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setResult(null);
    setError(null);
  }, [day]);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api<Result>(`/api/commentary?day=${day}`, { method: "POST" }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const stale = result && dataAsOf && result.data_as_of !== dataAsOf;
  const bullets = (result?.text ?? "")
    .split("\n")
    .map((line) => line.replace(/^\s*[-•*]\s*/, "").trim())
    .filter(Boolean);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Pusula yorumu</h2>
        <span className="hint">yapay zekâ · panel verisinden</span>
        <div className="spacer" />
        <button className="btn" onClick={run} disabled={busy}>
          {busy ? "Yorumlanıyor…" : result ? (stale ? "Yeni veriyle yorumla" : "Yenile") : "Yorumla"}
        </button>
      </div>
      <div className="card-body">
        {error ? <div className="banner error">{error}</div> : null}
        {result ? (
          <>
            <ul className="commentary">
              {bullets.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
            <div className="muted commentary-foot">
              {clock(result.data_as_of)} verisiyle · sayıları kod hesaplar, cümleleri yapay zekâ kurar; kararı sen verirsin.
            </div>
          </>
        ) : !error ? (
          <div className="empty">Günün durumunu birkaç maddede özetlemek için "Yorumla"ya bas.</div>
        ) : null}
      </div>
    </section>
  );
}
