import { useState, type FormEvent } from "react";

import { api, ApiError } from "../api";
import { Modal } from "../finance/ui";

/** Temsilci kendi şifresini değiştirir; ilk girişte (yöneticinin verdiği şifreyle) kendiliğinden açılır. */
export function PasswordModal({ first, onClose, onDone }: { first: boolean; onClose: () => void; onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (next !== again) {
      setError("Yeni şifreler aynı değil");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("/api/me/password", { method: "POST", body: JSON.stringify({ current, new: next }) });
      setDone(true);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bağlantı hatası");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Şifremi değiştir" onClose={onClose}>
      {done ? (
        <div className="pw-form">
          <p>Şifreniz değişti. Bir dahaki girişte yeni şifrenizi kullanın.</p>
          <button className="btn primary" onClick={onClose}>
            Tamam
          </button>
        </div>
      ) : (
        <form className="pw-form" onSubmit={submit}>
          {first ? <p className="muted">Size verilen ilk şifreyle girdiniz. Lütfen yalnız sizin bildiğiniz bir şifre belirleyin.</p> : null}
          <label>
            Mevcut şifre
            <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
          </label>
          <label>
            Yeni şifre (en az 8 karakter)
            <input type="password" autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} required />
          </label>
          <label>
            Yeni şifre (tekrar)
            <input type="password" autoComplete="new-password" minLength={8} value={again} onChange={(e) => setAgain(e.target.value)} required />
          </label>
          {error ? <div className="error">{error}</div> : null}
          <button className="btn primary" type="submit" disabled={busy}>
            {busy ? "Kaydediliyor…" : "Şifreyi değiştir"}
          </button>
        </form>
      )}
    </Modal>
  );
}
