import { Fragment, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { Modal } from "../finance/ui";
import { minutes } from "../format";

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
                        {r.role === "rep" ? (
                          <button
                            className="btn"
                            disabled={busy !== null}
                            title="Temsilcinin panelini onun gördüğü gibi açar (salt görüntüleme, 2 saat)"
                            onClick={() =>
                              api("/api/view-as", {
                                method: "POST",
                                body: JSON.stringify({ email: r.email }),
                              })
                                .then(() => {
                                  window.location.hash = "#/canli";
                                  window.location.reload();
                                })
                                .catch((err) =>
                                  setFailure(
                                    err instanceof ApiError
                                      ? err.message
                                      : "Bağlantı hatası",
                                  ),
                                )
                            }
                          >
                            Ekranını gör
                          </button>
                        ) : null}
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

      <UsageCard
        names={new Map((data?.users ?? []).map((u) => [u.email, u.full_name]))}
      />

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

interface UsageSession {
  basladi: string;
  bitti: string;
  aktif_sn: number;
  sayfalar: Record<string, number>;
}

interface UsagePerson {
  email: string;
  giris: number;
  son_giris: string | null;
  // Kendi şifresini değiştirdiği an (girdiğinin kanıtı; ölçüm öncesi de olabilir).
  sifre_degisti: string | null;
  // Bu kişinin ölçüm başlangıcı: kendi şifresini belirlediği an (daha erkense) ya da genel başlangıç.
  olcum_basi: string;
  son_gorulme: string | null;
  aktif_dk: number;
  aktif_gun: number;
  sayfalar: { sayfa: string; dk: number }[];
  oturumlar: UsageSession[];
}

interface Usage {
  gun: number;
  olcum_basi: string | null;
  kisiler: UsagePerson[];
}

const PAGE_LABEL: Record<string, string> = {
  canli: "Canlı",
  karsilastir: "Karşılaştır",
  pano: "OKRs",
  raporlar: "Raporlar",
  gorusmeler: "Görüşme Analizleri",
  ceo: "Yönetici özeti",
  kullanicilar: "Kullanıcılar",
};
const pageLabel = (p: string) =>
  PAGE_LABEL[p] ?? (p.startsWith("finans") ? "Finans" : p);
const dayFmt = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  timeZone: "Europe/Istanbul",
});
const timeFmt = new Intl.DateTimeFormat("tr-TR", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Istanbul",
});
const when = (iso: string | null) =>
  iso ? dateFmt.format(new Date(iso)) : "—";

/** Kim ne zaman girdi, panelde ne kadar aktif zaman geçirdi (sekme önde ve kullanılıyorken). */
function UsageCard({ names }: { names: Map<string, string> }) {
  const [days, setDays] = useState(7);
  const [open, setOpen] = useState<string | null>(null);
  const { data, error, refresh, loading } = usePolling<Usage>(
    `/api/usage?days=${days}`,
    5 * 60_000,
  );
  const rows = [...(data?.kisiler ?? [])].sort(
    (a, b) => b.aktif_dk - a.aktif_dk || b.giris - a.giris,
  );
  const silent = [...names.keys()].filter(
    (e) => !rows.some((r) => r.email === e),
  );

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-head">
        <h2>Panel kullanımı</h2>
        <div className="controls">
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          aria-label="Dönem"
        >
          <option value={1}>Son 24 saat</option>
          <option value={7}>Son 7 gün</option>
          <option value={30}>Son 30 gün</option>
          <option value={90}>Son 90 gün</option>
        </select>
        <button className="btn" onClick={refresh} disabled={loading}>
          {loading ? "Yenileniyor…" : "Yenile"}
        </button>
        </div>
      </div>
      <div className="sub" style={{ marginBottom: 8 }}>
        Aktif süre: sekme önde ve kişi son 5 dk içinde fare / klavye kullandıysa
        sayılır; açık unutulan sekme sayılmaz.
        {data?.olcum_basi
          ? ` Ölçüm ${when(data.olcum_basi)}'da başladı; öncesindeki girişler yalnız şifre değişikliğinden görünür. Sabahtan açık kalan sekmeler sayfa yenilenene kadar süre göndermez.`
          : ""}{" "}
        Sizin "Ekranını gör" görünümünüz sayılmaz.
      </div>
      {error ? (
        <div className="banner error">Kullanım alınamadı: {error.message}</div>
      ) : null}
      <table className="board">
        <thead>
          <tr>
            <th>Ad</th>
            <th>Son giriş</th>
            <th>Son görülme</th>
            <th className="num">Giriş</th>
            <th className="num">Aktif gün</th>
            <th className="num">Aktif süre</th>
            <th className="num">Günlük ort.</th>
            <th>En çok baktığı</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <Fragment key={r.email}>
              <tr
                onClick={() => setOpen(open === r.email ? null : r.email)}
                style={{ cursor: "pointer" }}
              >
                <td>
                  {open === r.email ? "▾ " : "▸ "}
                  {names.get(r.email) ?? r.email}
                </td>
                <td className="muted">
                  {when(r.son_giris)}
                  {r.sifre_degisti && r.son_giris === r.sifre_degisti ? " · şifresini belirledi" : ""}
                </td>
                <td className="muted">{when(r.son_gorulme)}</td>
                <td className="num">{r.giris}</td>
                <td className="num">{r.aktif_gun}</td>
                <td className="num">{minutes(r.aktif_dk)}</td>
                <td className="num">
                  {r.aktif_gun ? minutes(r.aktif_dk / r.aktif_gun) : "—"}
                </td>
                <td className="muted">
                  {r.sayfalar
                    .slice(0, 3)
                    .map((p) => `${pageLabel(p.sayfa)} ${minutes(p.dk)}`)
                    .join(" · ") || "—"}
                </td>
              </tr>
              {open === r.email ? (
                <tr>
                  <td colSpan={8}>
                    <div className="muted">
                      Ölçüm {when(r.olcum_basi)}'dan beri
                      {r.sifre_degisti && r.olcum_basi === r.sifre_degisti ? " (şifresini belirlediği an)" : ""}
                    </div>
                    {r.oturumlar.length ? (
                      <ul className="usage-sessions">
                        {r.oturumlar.map((o) => (
                          <li key={o.basladi}>
                            {dayFmt.format(new Date(o.basladi))}{" "}
                            {timeFmt.format(new Date(o.basladi))}–
                            {timeFmt.format(new Date(o.bitti))} · aktif{" "}
                            {minutes(o.aktif_sn / 60)} ·{" "}
                            {Object.entries(o.sayfalar)
                              .sort((a, b) => b[1] - a[1])
                              .map(([p, n]) => `${pageLabel(p)} ${n} dk`)
                              .join(", ")}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="muted">
                        Bu dönemde aktif oturum yok (girmiş ama panelde vakit
                        geçirmemiş).
                      </div>
                    )}
                  </td>
                </tr>
              ) : null}
            </Fragment>
          ))}
          {silent.map((e) => (
            <tr key={e} className="muted">
              <td>{names.get(e)}</td>
              <td colSpan={7}>
                {data?.olcum_basi && new Date(data.olcum_basi).getTime() > Date.now() - days * 86_400_000
                  ? `Ölçüm başladığından (${when(data.olcum_basi)}) beri kayıt yok`
                  : "Bu dönemde giriş yok"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
