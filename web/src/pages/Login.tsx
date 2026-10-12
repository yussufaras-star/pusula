import { useState, type FormEvent } from "react";

import { api, ApiError } from "../api";
import { Logo } from "../components/Logo";

export function LoginPage({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/login", { method: "POST", body: JSON.stringify({ email, password }) });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Bağlantı hatası");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card" onSubmit={submit}>
        <div className="brand">
          <Logo /> Pusula
        </div>
        <h1>Giriş</h1>
        <label>
          E-posta
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          Şifre
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? <div className="error">{error}</div> : null}
        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "Giriş yapılıyor…" : "Giriş yap"}
        </button>
      </form>
    </div>
  );
}
