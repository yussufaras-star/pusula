import { useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { Modal } from "../finance/ui";

interface UserRow {
  rep_id: string;
  full_name: string;
  email: string;
  kaynak: "panel" | "vercel" | null;
  updated_at: string | null;
  must_change: boolean | null;
}

interface Issued {
  email: string;
  full_name: string;
  password: string;
}

const PANEL_URL = "https://rexven-pusula.netlify.app";
const dateFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Istanbul" });

function status(r: UserRow): string {
  if (r.kaynak === "panel") return r.must_change ? "İlk şifre verildi, henüz değiştirmedi" : "Kendi şifresini belirledi";
  if (r.kaynak === "vercel") return "Eski şifre (Vercel)";
  return "Şifre yok, giremez";
}

/** Yönetici: temsilcilere ilk şifreyi verir; temsilci girince kendisi değiştirir. */
export function UsersPage() {
  const { data, error, refresh } = usePolling<UserRow[]>("/api/users", 0);
  const [busy, setBusy] = useState<string | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function issue(r: UserRow) {
    if (r.kaynak && !window.confirm(`${r.full_name} için yeni şifre üretilsin mi? Eski şifresi artık geçmez.`)) return;
    setBusy(r.rep_id);
    setFailure(null);
    try {
      setIssued(await api<Issued>("/api/users/password", { method: "POST", body: JSON.stringify({ rep_id: r.rep_id }) }));
      setCopied(false);
      refresh();
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "Bağlantı hatası");
    } finally {
      setBusy(null);
    }
  }

  const message = issued
    ? `Merhaba ${issued.full_name.split(" ")[0]}, Pusula giriş bilgilerin:\n${PANEL_URL}\nE-posta: ${issued.email}\nŞifre: ${issued.password}\nİlk girişte şifreni değiştirmen istenecek; yalnız senin bildiğin bir şifre belirle.`
    : "";

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Kullanıcılar</h1>
          <div className="sub">Temsilcilere ilk şifreyi buradan verin; temsilci ilk girişte kendi şifresini belirler. Şifreler şifrelenmiş saklanır, siz de göremezsiniz.</div>
        </div>
      </div>
      {error ? <div className="banner error">Kullanıcılar alınamadı: {error.message}</div> : null}
      {failure ? <div className="banner error">{failure}</div> : null}
      <div className="card">
        <table className="board">
          <thead>
            <tr>
              <th>Temsilci</th>
              <th>E-posta</th>
              <th>Durum</th>
              <th>Son değişiklik</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((r) => (
              <tr key={r.rep_id}>
                <td>{r.full_name}</td>
                <td className="muted">{r.email}</td>
                <td>{status(r)}</td>
                <td className="muted">{r.updated_at ? dateFmt.format(new Date(r.updated_at)) : "—"}</td>
                <td>
                  <button className="btn" onClick={() => issue(r)} disabled={busy === r.rep_id}>
                    {busy === r.rep_id ? "Üretiliyor…" : r.kaynak ? "Yeni şifre ver" : "Şifre ver"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {issued ? (
        <Modal title={`${issued.full_name} · ilk şifre`} onClose={() => setIssued(null)}>
          <div className="pw-form">
            <p>
              Şifre: <strong className="pw-issued">{issued.password}</strong>
            </p>
            <p className="muted">Bu şifre yalnız şimdi görünür. Aşağıdaki mesajı kopyalayıp temsilciye Cliq'ten özel mesajla gönderin.</p>
            <textarea readOnly value={message} rows={6} />
            <button
              className="btn primary"
              onClick={() => navigator.clipboard?.writeText(message).then(() => setCopied(true)).catch(() => undefined)}
            >
              {copied ? "Kopyalandı" : "Mesajı kopyala"}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
