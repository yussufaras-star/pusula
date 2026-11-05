import { useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { Modal } from "../finance/ui";

type Role = "rep" | "admin";

interface UserRow {
  email: string;
  rep_id: string;
  full_name: string;
  category: string;
  role: Role;
  active: boolean;
  ana: boolean;
  kaynak: "panel" | "vercel" | null;
  updated_at: string | null;
  must_change: boolean;
}

interface Candidate {
  email: string;
  full_name: string;
  category: string;
}

interface UsersData {
  users: UserRow[];
  candidates: Candidate[];
}

interface Issued {
  email: string;
  full_name: string;
  password: string;
}

const PANEL_URL = "https://rexven-pusula.netlify.app";
const ROLE_LABEL: Record<Role, string> = { rep: "Temsilci", admin: "Yönetici" };
const CATEGORY_LABEL: Record<string, string> = {
  sales: "satış",
  management: "yönetim",
  consultancy: "danışmanlık",
  other: "diğer",
};
const dateFmt = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Istanbul",
});

function status(r: UserRow): string {
  if (r.ana) return "Ana yönetici (şifre Vercel'de)";
  if (!r.active) return "Erişimi kapalı";
  if (r.kaynak === "panel")
    return r.must_change
      ? "İlk şifre verildi, henüz değiştirmedi"
      : "Kendi şifresini belirledi";
  if (r.kaynak === "vercel") return "Eski şifre (Vercel)";
  return "Şifre yok, giremez";
}

/** Yönetici: kullanıcı ekler / çıkarır, ilk şifreyi verir; kişi ilk girişte kendi şifresini belirler. */
export function UsersPage() {
  const { data, error, refresh } = usePolling<UsersData>("/api/users", 0);
  const [busy, setBusy] = useState<string | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [adding, setAdding] = useState(false);
  const [pick, setPick] = useState("");
  const [role, setRole] = useState<Role>("admin");

  async function run(
    key: string,
    path: string,
    body: object,
    confirmText?: string,
  ) {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(key);
    setFailure(null);
    try {
      const out = await api<Issued | { ok: string }>(path, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if ("password" in out) {
        setIssued(out);
        setCopied(false);
      }
      setAdding(false);
      setPick("");
      refresh();
    } catch (err) {
      setFailure(err instanceof ApiError ? err.message : "Bağlantı hatası");
    } finally {
      setBusy(null);
    }
  }

  // Satış ekibinden kapatılmış temsilciler de "Geri ekle" ile ya da buradan açılabilir.
  const options: Candidate[] = [
    ...(data?.users ?? [])
      .filter((u) => !u.active)
      .map((u) => ({
        email: u.email,
        full_name: u.full_name,
        category: u.category,
      })),
    ...(data?.candidates ?? []),
  ];
  const picked = options.find((o) => o.email === pick);
  const canRep = picked?.category === "sales";

  const message = issued
    ? `Merhaba ${issued.full_name.split(" ")[0]}, Pusula giriş bilgilerin:\n${PANEL_URL}\nE-posta: ${issued.email}\nŞifre: ${issued.password}\nİlk girişte şifreni değiştirmen istenecek; yalnız senin bildiğin bir şifre belirle.`
    : "";

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Kullanıcılar</h1>
          <div className="sub">
            Temsilci yalnız kendi verisini, yönetici her şeyi görür (finans ayrı
            izinle). İlk şifreyi siz verirsiniz; kişi ilk girişte kendi
            şifresini belirler. Şifreler şifrelenmiş saklanır, siz de
            göremezsiniz.
          </div>
        </div>
        <div className="controls">
          <button className="btn primary" onClick={() => setAdding(true)}>
            Kullanıcı ekle
          </button>
        </div>
      </div>
      {error ? (
        <div className="banner error">
          Kullanıcılar alınamadı: {error.message}
        </div>
      ) : null}
      {failure ? <div className="banner error">{failure}</div> : null}
      <div className="card">
        <table className="board">
          <thead>
            <tr>
              <th>Ad</th>
              <th>E-posta</th>
              <th>Rol</th>
              <th>Durum</th>
              <th>Son değişiklik</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(data?.users ?? []).map((r) => (
              <tr key={r.email} className={r.active ? undefined : "muted"}>
                <td>{r.full_name}</td>
                <td className="muted">{r.email}</td>
                <td>{ROLE_LABEL[r.role]}</td>
                <td>{status(r)}</td>
                <td className="muted">
                  {r.updated_at ? dateFmt.format(new Date(r.updated_at)) : "—"}
                </td>
                <td>
                  <div className="user-actions">
                    {r.ana ? null : r.active ? (
                      <>
                        <button
                          className="btn"
                          disabled={busy !== null}
                          onClick={() =>
                            run(
                              `pw:${r.email}`,
                              "/api/users/password",
                              { email: r.email },
                              r.kaynak
                                ? `${r.full_name} için yeni şifre üretilsin mi? Eski şifresi artık geçmez.`
                                : undefined,
                            )
                          }
                        >
                          {busy === `pw:${r.email}`
                            ? "Üretiliyor…"
                            : r.kaynak
                              ? "Yeni şifre ver"
                              : "Şifre ver"}
                        </button>
                        <button
                          className="btn"
                          disabled={busy !== null}
                          onClick={() =>
                            run(
                              `rm:${r.email}`,
                              "/api/users/remove",
                              { email: r.email },
                              `${r.full_name} panelden çıkarılsın mı? Girişi ve açık oturumu hemen kapanır.`,
                            )
                          }
                        >
                          {busy === `rm:${r.email}` ? "Çıkarılıyor…" : "Çıkar"}
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn"
                        disabled={busy !== null}
                        onClick={() =>
                          run(`add:${r.email}`, "/api/users", {
                            email: r.email,
                            role: r.role,
                          })
                        }
                      >
                        {busy === `add:${r.email}` ? "Ekleniyor…" : "Geri ekle"}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {adding ? (
        <Modal title="Kullanıcı ekle" onClose={() => setAdding(false)}>
          <div className="pw-form">
            <label>
              Kişi (Zoho kullanıcıları)
              <select
                value={pick}
                onChange={(e) => {
                  setPick(e.target.value);
                  const c = options.find((o) => o.email === e.target.value);
                  setRole(c?.category === "sales" ? "rep" : "admin");
                }}
              >
                <option value="">Seçin…</option>
                {options.map((o) => (
                  <option key={o.email} value={o.email}>
                    {o.full_name} · {CATEGORY_LABEL[o.category] ?? o.category}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Rol
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as Role)}
                disabled={!picked}
              >
                <option value="rep" disabled={!canRep}>
                  Temsilci — yalnız kendi verisi
                  {canRep ? "" : " (yalnız satış ekibi)"}
                </option>
                <option value="admin">Yönetici — her şey (finans hariç)</option>
              </select>
            </label>
            <p className="muted">
              Eklenince ilk şifre üretilir; kişiye Cliq'ten özel mesajla
              gönderirsiniz.
            </p>
            <button
              className="btn primary"
              disabled={!picked || busy !== null}
              onClick={() =>
                picked &&
                run("add", "/api/users", { email: picked.email, role })
              }
            >
              {busy === "add" ? "Ekleniyor…" : "Ekle ve şifre üret"}
            </button>
          </div>
        </Modal>
      ) : null}

      {issued ? (
        <Modal
          title={`${issued.full_name} · ilk şifre`}
          onClose={() => setIssued(null)}
        >
          <div className="pw-form">
            <p>
              Şifre: <strong className="pw-issued">{issued.password}</strong>
            </p>
            <p className="muted">
              Bu şifre yalnız şimdi görünür. Aşağıdaki mesajı kopyalayıp kişiye
              Cliq'ten özel mesajla gönderin.
            </p>
            <textarea readOnly value={message} rows={6} />
            <button
              className="btn primary"
              onClick={() =>
                navigator.clipboard
                  ?.writeText(message)
                  .then(() => setCopied(true))
                  .catch(() => undefined)
              }
            >
              {copied ? "Kopyalandı" : "Mesajı kopyala"}
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
