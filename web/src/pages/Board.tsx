import { useRef, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { StatTile, ToneBadge } from "../components/StatTile";
import { Field, Modal } from "../finance/ui";
import { duration, num, pct, tlShort, type Tone } from "../format";
import type {
  CfrKind,
  KrStatus,
  Me,
  OkrBoard,
  OkrKeyResult,
  OkrKind,
  OkrObjective,
  OkrStatus,
  OkrUnit,
} from "../types";

// Ekip Panosu: John Doerr'in "Measure What Matters" usulünde OKR.
// Hesap pusula/okr.py'dedir; burada yalnız gösterim ve formlar var.

const QUARTERS: { q: number; label: string }[] = [
  { q: 0, label: "Yıllık" },
  { q: 1, label: "Ç1" },
  { q: 2, label: "Ç2" },
  { q: 3, label: "Ç3" },
  { q: 4, label: "Ç4" },
];

const KIND_LABEL: Record<OkrKind, string> = { taahhut: "Taahhüt", iddiali: "İddialı" };
const KIND_HINT: Record<OkrKind, string> = {
  taahhut: "1,0 beklenir; olmazsa olmaz",
  iddiali: "0,7 başarıdır; esneme hedefi",
};
const STATUS_LABEL: Record<OkrStatus, string> = { taslak: "Taslak", aktif: "Aktif", kapandi: "Kapandı" };
const UNIT_LABEL: Record<OkrUnit, string> = {
  tl: "TL",
  adet: "adet",
  yuzde: "%",
  sn: "saniye",
  gun: "gün",
  sayi: "sayı",
};
const KR_STATUS: Record<KrStatus, [string, Tone]> = {
  baslamadi: ["Başlamadı", "neutral"],
  veri_yok: ["Veri yok", "neutral"],
  yolunda: ["Yolunda", "good"],
  risk: ["Risk", "warning"],
  geride: ["Geride", "critical"],
  tamam: ["Tamam", "good"],
  kacti: ["Kaçtı", "critical"],
};
const CFR_LABEL: Record<CfrKind, string> = {
  takdir: "Takdir",
  konusma: "Konuşma",
  geri_bildirim: "Geri bildirim",
};

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "Europe/Istanbul" });
const shortDay = (iso: string) => dayFmt.format(new Date(iso.length === 10 ? `${iso}T12:00:00+03:00` : iso));

function fmtValue(v: number | null, unit: OkrUnit): string {
  if (v === null) return "—";
  if (unit === "tl") return tlShort(v);
  if (unit === "yuzde") return pct(v);
  if (unit === "sn") return duration(v);
  if (unit === "gun") return `${num(v)} gün`;
  return num(v);
}

const grade = (s: number | null | undefined) =>
  s === null || s === undefined ? "—" : s.toLocaleString("tr-TR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function scoreTone(s: number | null): Tone {
  if (s === null) return "neutral";
  if (s >= 0.7) return "good";
  return s >= 0.4 ? "warning" : "critical";
}

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

type Editing =
  | { type: "objective"; objective: OkrObjective | null; owner: string }
  | { type: "kr"; objective: OkrObjective; kr: OkrKeyResult | null }
  | { type: "checkin"; objective: OkrObjective; kr: OkrKeyResult }
  | { type: "close"; objective: OkrObjective };

function istanbulToday(): { year: number; quarter: number } {
  const s = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
  const [y, m] = s.split("-").map(Number);
  return { year: y, quarter: Math.floor((m - 1) / 3) + 1 };
}

export function BoardPage({ me }: { me: Me }) {
  const now = istanbulToday();
  const [year, setYear] = useState(now.year);
  const [quarter, setQuarter] = useState(now.quarter);
  const [tick, setTick] = useState(0);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [guide, setGuide] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const { data, error } = usePolling<OkrBoard>(`/api/okr?year=${year}&quarter=${quarter}&_=${tick}`, 5 * 60_000);
  const refresh = () => setTick((n) => n + 1);
  const isAdmin = me.role === "admin";

  const canEdit = (owner: string) => isAdmin || (owner !== "team" && owner === me.rep_id);
  const canCheckin = (o: OkrObjective, kr: OkrKeyResult) =>
    isAdmin || o.owner === me.rep_id || (kr.owner_rep_id !== null && kr.owner_rep_id === me.rep_id);

  async function seed() {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/okr/seed", { method: "POST", body: JSON.stringify({ year }) });
      refresh();
    } catch (e) {
      setErr(errText(e, "Taslak yüklenemedi"));
    } finally {
      setBusy(false);
    }
  }

  function tv() {
    const el = wrap.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void el.requestFullscreen?.();
  }

  const team = data?.objectives.filter((o) => o.owner === "team") ?? [];
  const personal = data?.objectives.filter((o) => o.owner !== "team") ?? [];
  const reps = data?.reps ?? [];

  return (
    <div ref={wrap} className="okr-page">
      <div className="page-head">
        <div>
          <h1>Ekip Panosu</h1>
          <div className="sub">
            Hedefler ve anahtar sonuçlar (OKR) — John Doerr, <i>Measure What Matters</i>
          </div>
        </div>
        <div className="controls">
          <select className="f-input sm" value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Yıl">
            {[now.year - 1, now.year, now.year + 1].map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
          <div className="seg" role="tablist" aria-label="Dönem">
            {QUARTERS.map(({ q, label }) => (
              <button key={q} role="tab" aria-selected={quarter === q} onClick={() => setQuarter(q)}>
                {label}
                {data && data.year === year && data.counts[String(q)] ? (
                  <span className="okr-count">{data.counts[String(q)]}</span>
                ) : null}
              </button>
            ))}
          </div>
          <button className="btn" onClick={() => setGuide((g) => !g)} aria-expanded={guide}>
            Nasıl çalışır
          </button>
          <button className="btn" onClick={tv} title="Ofis ekranı için tam ekran">
            TV modu
          </button>
        </div>
      </div>

      {guide ? <Guide /> : null}
      {err ? <div className="banner error">{err}</div> : null}
      {error ? <div className="banner error">Pano alınamadı: {error.message}</div> : null}

      {!data ? (
        <div className="tiles">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      ) : (
        <>
          <PeriodStrip board={data} />

          <div className="tiles okr-tiles">
            <StatTile
              label="Ekip puanı"
              value={grade(data.summary.team_score)}
              unit="/ 1,0"
              tone={scoreTone(data.summary.team_score)}
              badge={data.summary.team_score === null ? undefined : data.summary.team_score >= 0.7 ? "başarılı" : "takip"}
              expected="ekip hedeflerinin ortalaması; 0,7+ başarı"
            />
            <StatTile
              label="Yolunda"
              value={`${data.summary.on_track} / ${data.summary.krs}`}
              expected="anahtar sonuç"
              tone="good"
            />
            <StatTile
              label="Risk / geride"
              value={data.summary.at_risk}
              tone={data.summary.at_risk ? "warning" : "neutral"}
              badge={data.summary.at_risk ? "konuşulmalı" : undefined}
              expected="tempo beklenenin altında"
            />
            <StatTile
              label="Check-in bekleyen"
              value={data.summary.checkin_due}
              tone={data.summary.checkin_due ? "warning" : "neutral"}
              badge={data.summary.checkin_due ? "haftalık" : undefined}
              expected="7 gündür güncellenmeyen"
            />
          </div>

          {data.warnings.length ? (
            <div className="banner">
              <b>Odak uyarısı.</b>
              <ul className="okr-warn">
                {data.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <section className="okr-section">
            <div className="okr-section-head">
              <h2>{quarter === 0 ? "Ekibin yıllık hedefleri" : "Ekip hedefleri"}</h2>
              <div className="spacer" />
              {data.template_available ? (
                <button className="btn" onClick={seed} disabled={busy}>
                  {busy ? "Yükleniyor…" : `Önerilen ${year} taslağını yükle`}
                </button>
              ) : null}
              {isAdmin ? (
                <button className="btn primary" onClick={() => setEditing({ type: "objective", objective: null, owner: "team" })}>
                  Hedef ekle
                </button>
              ) : null}
            </div>
            {team.length ? (
              team.map((o) => (
                <ObjectiveCard
                  key={o.id}
                  o={o}
                  canEdit={canEdit(o.owner)}
                  canCheckin={(kr) => canCheckin(o, kr)}
                  onEdit={setEditing}
                />
              ))
            ) : (
              <div className="card empty okr-empty">
                {data.template_available
                  ? "Bu yıl için henüz OKR yok. Önerilen taslak 2026 verisinden hazırlandı; yükleyip panoda düzenleyebilirsin."
                  : "Bu dönem için ekip hedefi yok."}
              </div>
            )}
          </section>

          {quarter !== 0 ? (
            <section className="okr-section">
              <div className="okr-section-head">
                <h2>Kişisel hedefler</h2>
                <span className="muted">Doerr: hedeflerin bir kısmı aşağıdan yukarı gelir; temsilci kendi OKR'ını yazar.</span>
              </div>
              {reps.map((r) => {
                const own = personal.filter((o) => o.owner === r.rep_id);
                return (
                  <div key={r.rep_id} className="okr-rep">
                    <div className="okr-rep-head">
                      <b>{r.name}</b>
                      <span className="muted">{own.length ? `${own.length} hedef` : "henüz kişisel hedef yok"}</span>
                      <div className="spacer" />
                      {canEdit(r.rep_id) ? (
                        <button
                          className="btn sm"
                          onClick={() => setEditing({ type: "objective", objective: null, owner: r.rep_id })}
                        >
                          Kişisel hedef ekle
                        </button>
                      ) : null}
                    </div>
                    {own.map((o) => (
                      <ObjectiveCard
                        key={o.id}
                        o={o}
                        canEdit={canEdit(o.owner)}
                        canCheckin={(kr) => canCheckin(o, kr)}
                        onEdit={setEditing}
                      />
                    ))}
                  </div>
                );
              })}
            </section>
          ) : null}

          <CfrCard board={data} me={me} onSaved={refresh} />
        </>
      )}

      {editing && data ? (
        <EditModal editing={editing} board={data} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refresh(); }} />
      ) : null}
    </div>
  );
}

function PeriodStrip({ board }: { board: OkrBoard }) {
  const text =
    board.state === "gelecek"
      ? "henüz başlamadı"
      : board.state === "bitti"
        ? "dönem bitti — puanla ve dersleri yaz"
        : `dönemin %${Math.round(board.share * 100)}'i geçti`;
  return (
    <div className="card okr-period">
      <div>
        <b>{board.label}</b> · {shortDay(board.first)} – {shortDay(board.last)} · <span className="muted">{text}</span>
      </div>
      <div className="meter" aria-hidden>
        <div className="fill" style={{ width: `${board.share * 100}%` }} />
      </div>
    </div>
  );
}

function ObjectiveCard({
  o,
  canEdit,
  canCheckin,
  onEdit,
}: {
  o: OkrObjective;
  canEdit: boolean;
  canCheckin: (kr: OkrKeyResult) => boolean;
  onEdit: (e: Editing) => void;
}) {
  const shown = o.final_score ?? o.score;
  return (
    <article className={`card okr-obj${o.status === "taslak" ? " draft" : ""}`}>
      <div className="okr-obj-head">
        <div className={`okr-score ${o.tone}`} title={o.final_score !== null ? "Kapanış puanı" : "Anahtar sonuçların ortalaması"}>
          <span>{grade(shown)}</span>
          <small>{o.final_score !== null ? "kapanış" : "puan"}</small>
        </div>
        <div className="okr-obj-main">
          <div className="okr-tags">
            <span className={`badge ${o.kind === "taahhut" ? "neutral" : "warning"}`} title={KIND_HINT[o.kind]}>
              {KIND_LABEL[o.kind]}
            </span>
            {o.status !== "aktif" ? <span className="badge neutral">{STATUS_LABEL[o.status]}</span> : null}
            {o.parent_title ? <span className="okr-parent">↑ {o.parent_title}</span> : null}
            {o.children ? <span className="okr-parent">↓ {o.children} bağlı hedef</span> : null}
          </div>
          <h3>{o.title}</h3>
          {o.description ? <p className="muted">{o.description}</p> : null}
          {o.reflection ? (
            <p className="okr-reflection">
              <b>Kapanış notu:</b> {o.reflection}
            </p>
          ) : null}
        </div>
        {canEdit ? (
          <div className="okr-actions">
            <button className="btn sm" onClick={() => onEdit({ type: "kr", objective: o, kr: null })}>
              + Anahtar sonuç
            </button>
            <button className="btn sm" onClick={() => onEdit({ type: "objective", objective: o, owner: o.owner })}>
              Düzenle
            </button>
            <button className="btn sm" onClick={() => onEdit({ type: "close", objective: o })}>
              Puanla
            </button>
          </div>
        ) : null}
      </div>
      {o.krs.length ? (
        <ul className="okr-krs">
          {o.krs.map((kr) => (
            <KrRow
              key={kr.id}
              kr={kr}
              canEdit={canEdit}
              canCheckin={canCheckin(kr)}
              onCheckin={() => onEdit({ type: "checkin", objective: o, kr })}
              onEdit={() => onEdit({ type: "kr", objective: o, kr })}
            />
          ))}
        </ul>
      ) : (
        <div className="empty">Anahtar sonuç yok. Doerr: sayısı yoksa anahtar sonuç değildir.</div>
      )}
    </article>
  );
}

function KrRow({
  kr,
  canEdit,
  canCheckin,
  onCheckin,
  onEdit,
}: {
  kr: OkrKeyResult;
  canEdit: boolean;
  canCheckin: boolean;
  onCheckin: () => void;
  onEdit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [label, tone] = KR_STATUS[kr.status];
  const progress = kr.progress ?? 0;
  const fill = tone === "neutral" ? "" : tone;
  const last = kr.checkins[0];
  return (
    <li className="okr-kr">
      <div className="okr-kr-top">
        <div className="okr-kr-title">
          <span>{kr.title}</span>
          <span className="okr-src">
            {kr.source === "otomatik"
              ? `otomatik · ${kr.metric_label}`
              : kr.source === "yedek"
                ? `${kr.metric_label} · bu dönem ölçülmedi, check-in ile gir`
                : "elle · check-in ile"}
            {kr.phasing ? " · aylık plana göre tempo" : ""}
            {kr.owner_name ? ` · sorumlu ${kr.owner_name}` : ""}
          </span>
          {kr.note ? <span className="okr-src">{kr.note}</span> : null}
        </div>
        <div className="okr-kr-score">
          <b className={`okr-grade ${tone}`}>{grade(kr.score)}</b>
          <ToneBadge tone={tone}>{label}</ToneBadge>
        </div>
      </div>
      <div className="meter okr-meter" title={kr.expected !== null ? `Beklenen: ${Math.round(kr.expected * 100)}%` : undefined}>
        <div className={`fill ${fill}`} style={{ width: `${Math.min(progress, 1) * 100}%` }} />
        {kr.expected !== null && kr.expected < 1 ? <div className="mark" style={{ left: `${kr.expected * 100}%` }} /> : null}
      </div>
      <div className="okr-kr-foot">
        <span>
          <b>{fmtValue(kr.current, kr.unit)}</b> / {fmtValue(kr.target_value, kr.unit)}
          <span className="muted"> · başlangıç {fmtValue(kr.start_value, kr.unit)}</span>
        </span>
        {kr.confidence !== null ? (
          <span className={`okr-conf ${kr.confidence >= 7 ? "good" : kr.confidence >= 4 ? "warning" : "critical"}`}>
            güven {kr.confidence}/10
          </span>
        ) : null}
        {kr.checkin_due ? <span className="badge warning">check-in bekliyor</span> : null}
        <div className="spacer" />
        {kr.checkins.length ? (
          <button className="link-btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? "Geçmişi gizle" : `Check-in geçmişi (${kr.checkins.length})`}
          </button>
        ) : null}
        {canCheckin ? (
          <button className="btn sm primary" onClick={onCheckin}>
            Check-in
          </button>
        ) : null}
        {canEdit ? (
          <button className="btn sm" onClick={onEdit}>
            Düzenle
          </button>
        ) : null}
      </div>
      {last?.note && !open ? (
        <div className="okr-last">
          “{last.note}” <span className="muted">— {last.author}, {shortDay(last.created_at)}</span>
        </div>
      ) : null}
      {open ? (
        <ul className="okr-history">
          {kr.checkins.map((c) => (
            <li key={c.id}>
              <span className="muted">{shortDay(c.created_at)}</span>
              {c.value !== null ? <b>{fmtValue(c.value, kr.unit)}</b> : null}
              {c.confidence !== null ? <span>güven {c.confidence}/10</span> : null}
              {c.note ? <span>“{c.note}”</span> : null}
              <span className="muted">{c.author}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

function CfrCard({ board, me, onSaved }: { board: OkrBoard; me: Me; onSaved: () => void }) {
  const isAdmin = me.role === "admin";
  const [filter, setFilter] = useState<CfrKind | "hepsi">("hepsi");
  const [kind, setKind] = useState<CfrKind>("takdir");
  const [rep, setRep] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const kinds: CfrKind[] = isAdmin ? ["takdir", "konusma", "geri_bildirim"] : ["takdir", "konusma"];
  const rows = board.cfr.filter((c) => filter === "hepsi" || c.kind === filter);
  // Takdirde herkes listelenir; temsilci yalnız kendisi için konuşma notu yazar.
  const repOptions = isAdmin || kind === "takdir" ? board.reps : board.reps.filter((r) => r.rep_id === me.rep_id);

  async function save() {
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api("/api/okr/cfr", {
        method: "POST",
        body: JSON.stringify({ kind, rep_id: (kind === "konusma" && !isAdmin ? me.rep_id : rep) || null, text: text.trim() }),
      });
      setText("");
      onSaved();
    } catch (e) {
      setErr(errText(e, "Kaydedilemedi"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Konuşma, geri bildirim, takdir</h2>
        <span className="hint">Doerr'in CFR'ı: OKR'ı yıllık değerlendirme değil haftalık sohbet yaşatır.</span>
      </div>
      <div className="card-body">
        <div className="okr-cfr-form">
          <select className="f-input sm" value={kind} onChange={(e) => setKind(e.target.value as CfrKind)} aria-label="Tür">
            {kinds.map((k) => (
              <option key={k} value={k}>
                {CFR_LABEL[k]}
              </option>
            ))}
          </select>
          {isAdmin || kind === "takdir" ? (
            <select className="f-input sm" value={rep} onChange={(e) => setRep(e.target.value)} aria-label="Kim için">
              <option value="">{kind === "takdir" ? "Tüm ekip" : "Kişi seç"}</option>
              {repOptions.map((r) => (
                <option key={r.rep_id} value={r.rep_id}>
                  {r.name}
                </option>
              ))}
            </select>
          ) : null}
          <input
            className="f-input"
            value={text}
            maxLength={2000}
            placeholder={
              kind === "takdir"
                ? "Neyi takdir ediyorsun? Somut ol: hangi davranış, hangi sonuç."
                : kind === "konusma"
                  ? "Birebir görüşme notu: ne konuşuldu, sonraki adım ne?"
                  : "Geri bildirim: gözlem, etkisi, beklenti."
            }
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
          <button className="btn primary" onClick={save} disabled={busy || !text.trim() || (kind !== "takdir" && isAdmin && !rep)}>
            Ekle
          </button>
        </div>
        {err ? <div className="banner error">{err}</div> : null}
        <div className="chips okr-cfr-filter">
          {(["hepsi", "takdir", "konusma", "geri_bildirim"] as const).map((k) => (
            <button key={k} className={`chip${filter === k ? " on" : ""}`} onClick={() => setFilter(k)}>
              {k === "hepsi" ? "Hepsi" : CFR_LABEL[k]}
            </button>
          ))}
        </div>
        {rows.length ? (
          <ul className="okr-cfr">
            {rows.map((c) => (
              <li key={c.id} className={c.kind}>
                <span className="okr-cfr-kind">{CFR_LABEL[c.kind]}</span>
                <div>
                  <div>{c.text}</div>
                  <div className="muted">
                    {c.author}
                    {c.rep_name ? ` → ${c.rep_name}` : c.kind === "takdir" ? " → ekip" : ""} · {shortDay(c.created_at)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty">Henüz not yok. İlk takdiri sen yaz.</div>
        )}
      </div>
    </section>
  );
}

function Guide() {
  return (
    <section className="card okr-guide">
      <div className="card-head">
        <h2>Doerr usulü OKR nasıl çalışır?</h2>
      </div>
      <div className="card-body">
        <div className="okr-guide-grid">
          <div>
            <h3>Formül</h3>
            <p>
              <b>Hedef</b> (ne istiyoruz, nitel ve ilham verici) <b>şununla ölçülür:</b> 2-5 <b>anahtar sonuç</b> (sayısal,
              tarihli, doğrulanabilir). Sayısı yoksa anahtar sonuç değildir.
            </p>
          </div>
          <div>
            <h3>1 · Odak</h3>
            <p>Çeyrek başına en fazla 3-5 hedef. Her şey önemliyse hiçbir şey önemli değildir; pano fazlasında uyarır.</p>
          </div>
          <div>
            <h3>2 · Hizalama</h3>
            <p>Çeyrek hedefleri yıllık hedefe, kişisel hedefler ekip hedefine bağlanır (↑). Temsilci kendi OKR'ını kendisi önerir.</p>
          </div>
          <div>
            <h3>3 · Takip</h3>
            <p>
              Ciro, satış, oranlar Pusula verisinden otomatik ölçülür. Her hafta check-in: elle ölçülenlere değer, hepsine 1-10 güven
              ve kısa not.
            </p>
          </div>
          <div>
            <h3>4 · Esneme</h3>
            <p>
              <b>Taahhüt</b> hedefinde 1,0 beklenir. <b>İddialı</b> hedefte 0,7 başarıdır; hep 1,0 alınıyorsa hedef yeterince
              büyük değildir.
            </p>
          </div>
          <div>
            <h3>Puan</h3>
            <p>
              0,0-1,0. <span className="okr-grade good">0,7-1,0</span> teslim edildi, <span className="okr-grade warning">0,4-0,6</span>{" "}
              ilerleme var, <span className="okr-grade critical">0,0-0,3</span> olmadı. Çubuktaki çizgi zamana göre beklenen yer.
            </p>
          </div>
          <div>
            <h3>Ritim</h3>
            <p>
              Çeyrek başı: OKR yaz. Her pazartesi: check-in + birebir konuşma. Çeyrek ortası: gerekirse hedefi güncelle. Çeyrek
              sonu: puanla, kapanış notu yaz, sonraki çeyreğe taşı.
            </p>
          </div>
          <div>
            <h3>CFR</h3>
            <p>Konuşma, geri bildirim, takdir. OKR prim hesabı değildir; puan ücretle bağlanmaz, öğrenmek için tutulur.</p>
          </div>
        </div>
      </div>
    </section>
  );
}

// --- formlar -----------------------------------------------------------------------

function EditModal({
  editing,
  board,
  onClose,
  onSaved,
}: {
  editing: Editing;
  board: OkrBoard;
  onClose: () => void;
  onSaved: () => void;
}) {
  if (editing.type === "objective")
    return <ObjectiveForm objective={editing.objective} owner={editing.owner} board={board} onClose={onClose} onSaved={onSaved} />;
  if (editing.type === "kr") return <KrForm objective={editing.objective} kr={editing.kr} board={board} onClose={onClose} onSaved={onSaved} />;
  if (editing.type === "checkin") return <CheckinForm kr={editing.kr} onClose={onClose} onSaved={onSaved} />;
  return <CloseForm objective={editing.objective} onClose={onClose} onSaved={onSaved} />;
}

function objectiveBody(o: OkrObjective) {
  return {
    year: o.year,
    quarter: o.quarter,
    owner: o.owner,
    title: o.title,
    description: o.description,
    kind: o.kind,
    status: o.status,
    parent_id: o.parent_id,
    final_score: o.final_score,
    reflection: o.reflection,
    sort: o.sort,
  };
}

function useSave(onSaved: () => void) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run(path: string, method: string, body?: unknown) {
    setBusy(true);
    setErr(null);
    try {
      await api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      onSaved();
    } catch (e) {
      setErr(errText(e, "Kaydedilemedi"));
      setBusy(false);
    }
  }
  return { busy, err, setErr, run };
}

function Actions({ busy, onClose, onSave, onDelete }: { busy: boolean; onClose: () => void; onSave: () => void; onDelete?: () => void }) {
  return (
    <div className="f-actions">
      {onDelete ? (
        <button className="btn danger" onClick={onDelete} disabled={busy}>
          Sil
        </button>
      ) : null}
      <div className="spacer" />
      <button className="btn" onClick={onClose}>
        Vazgeç
      </button>
      <button className="btn primary" onClick={onSave} disabled={busy}>
        {busy ? "Kaydediliyor…" : "Kaydet"}
      </button>
    </div>
  );
}

function ObjectiveForm({
  objective,
  owner,
  board,
  onClose,
  onSaved,
}: {
  objective: OkrObjective | null;
  owner: string;
  board: OkrBoard;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState({
    title: objective?.title ?? "",
    description: objective?.description ?? "",
    kind: objective?.kind ?? ("taahhut" as OkrKind),
    status: objective?.status ?? ("aktif" as OkrStatus),
    parent_id: objective?.parent_id ? String(objective.parent_id) : "",
  });
  const { busy, err, setErr, run } = useSave(onSaved);
  const parents = board.aligned_parents.filter((p) => p.id !== objective?.id && (owner !== "team" || p.quarter === 0));
  const ownerName = owner === "team" ? "Ekip" : (board.reps.find((r) => r.rep_id === owner)?.name ?? owner);

  function save() {
    if (!f.title.trim()) {
      setErr("Hedef cümlesi gerekli.");
      return;
    }
    const base = objective ? objectiveBody(objective) : { year: board.year, quarter: board.quarter, owner, sort: 0, final_score: null, reflection: null };
    const body = {
      ...base,
      title: f.title.trim(),
      description: f.description.trim() || null,
      kind: f.kind,
      status: f.status,
      parent_id: f.parent_id ? Number(f.parent_id) : null,
    };
    void run(objective ? `/api/okr/objectives/${objective.id}` : "/api/okr/objectives", objective ? "PATCH" : "POST", body);
  }

  function remove() {
    if (!objective || !window.confirm("Hedef ve anahtar sonuçları silinsin mi?")) return;
    void run(`/api/okr/objectives/${objective.id}`, "DELETE");
  }

  return (
    <Modal title={`${objective ? "Hedefi düzenle" : "Hedef ekle"} · ${ownerName} · ${board.label}`} onClose={onClose} wide>
      <Field label="Hedef (Objective)" hint="Ne başarmak istiyoruz? Nitel, ilham verici, tek cümle. Sayı anahtar sonuçlara gider.">
        <input className="f-input" value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} />
      </Field>
      <Field label="Neden önemli?" hint="Ekibin bu hedefe neden inanması gerektiği.">
        <textarea className="f-input" rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </Field>
      <div className="f-grid">
        <Field label="Tür" hint={KIND_HINT[f.kind]}>
          <select className="f-input" value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as OkrKind })}>
            <option value="taahhut">Taahhüt</option>
            <option value="iddiali">İddialı</option>
          </select>
        </Field>
        <Field label="Durum">
          <select className="f-input" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value as OkrStatus })}>
            {(Object.keys(STATUS_LABEL) as OkrStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </Field>
        {board.quarter !== 0 || owner !== "team" ? (
          <Field label="Hizalandığı hedef" hint="Bu hedef hangi üst hedefe hizmet ediyor?">
            <select className="f-input" value={f.parent_id} onChange={(e) => setF({ ...f, parent_id: e.target.value })}>
              <option value="">Bağımsız</option>
              {parents.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.quarter === 0 ? "Yıllık" : "Ekip"} · {p.title}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
      </div>
      {err ? <div className="banner error">{err}</div> : null}
      <Actions busy={busy} onClose={onClose} onSave={save} onDelete={objective ? remove : undefined} />
    </Modal>
  );
}

function KrForm({
  objective,
  kr,
  board,
  onClose,
  onSaved,
}: {
  objective: OkrObjective;
  kr: OkrKeyResult | null;
  board: OkrBoard;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [f, setF] = useState({
    title: kr?.title ?? "",
    metric: kr?.metric ?? "",
    unit: kr?.unit ?? ("sayi" as OkrUnit),
    direction: kr?.direction ?? ("up" as "up" | "down"),
    start_value: kr ? String(kr.start_value) : "0",
    target_value: kr ? String(kr.target_value) : "",
    owner_rep_id: kr?.owner_rep_id ?? "",
  });
  const { busy, err, setErr, run } = useSave(onSaved);
  const metric = board.metrics.find((m) => m.key === f.metric);
  const isTeam = objective.owner === "team";

  function save() {
    if (!f.title.trim() || f.target_value === "") {
      setErr("Başlık ve hedef değer gerekli.");
      return;
    }
    const body = {
      objective_id: objective.id,
      title: f.title.trim(),
      metric: f.metric || null,
      unit: metric ? metric.unit : f.unit,
      direction: metric ? metric.direction : f.direction,
      start_value: Number(f.start_value || 0),
      target_value: Number(f.target_value),
      owner_rep_id: isTeam ? f.owner_rep_id || null : null,
      phasing: kr?.phasing ?? null,
      sort: kr?.sort ?? objective.krs.length,
    };
    void run(kr ? `/api/okr/key-results/${kr.id}` : "/api/okr/key-results", kr ? "PATCH" : "POST", body);
  }

  function remove() {
    if (!kr || !window.confirm("Anahtar sonuç ve check-in geçmişi silinsin mi?")) return;
    void run(`/api/okr/key-results/${kr.id}`, "DELETE");
  }

  return (
    <Modal title={kr ? "Anahtar sonucu düzenle" : "Anahtar sonuç ekle"} onClose={onClose} wide>
      <p className="muted okr-form-note">“{objective.title}” hedefi şununla ölçülür:</p>
      <Field label="Anahtar sonuç (Key Result)" hint="Sayısal ve doğrulanabilir: ‘Çeyrek cirosu 10 M TL’, ‘Katılım %36'dan %55'e’.">
        <input className="f-input" value={f.title} maxLength={200} onChange={(e) => setF({ ...f, title: e.target.value })} />
      </Field>
      <div className="f-grid">
        <Field
          label="Ölçüm"
          hint={
            metric
              ? `${isTeam ? "Ekip toplamı" : "Temsilcinin kendi verisi"}, dönem başından bugüne otomatik.`
              : "Elle: değer haftalık check-in'de girilir."
          }
        >
          <select className="f-input" value={f.metric} onChange={(e) => setF({ ...f, metric: e.target.value })}>
            <option value="">Elle (check-in ile)</option>
            {board.metrics.map((m) => (
              <option key={m.key} value={m.key}>
                Otomatik · {m.label}
              </option>
            ))}
          </select>
        </Field>
        {!metric ? (
          <Field label="Birim">
            <select className="f-input" value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value as OkrUnit })}>
              {(Object.keys(UNIT_LABEL) as OkrUnit[]).map((u) => (
                <option key={u} value={u}>
                  {UNIT_LABEL[u]}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
        <Field label="Başlangıç" hint="Dönem başındaki değer (birikimli metrikte 0).">
          <input className="f-input num" type="number" value={f.start_value} onChange={(e) => setF({ ...f, start_value: e.target.value })} />
        </Field>
        <Field label="Hedef" hint={metric?.direction === "down" || f.direction === "down" ? "Azalması iyi: hedef başlangıçtan küçük." : undefined}>
          <input className="f-input num" type="number" value={f.target_value} onChange={(e) => setF({ ...f, target_value: e.target.value })} />
        </Field>
        {isTeam ? (
          <Field label="Sorumlu" hint="Sorumlu temsilci bu sonuca check-in yapabilir.">
            <select className="f-input" value={f.owner_rep_id} onChange={(e) => setF({ ...f, owner_rep_id: e.target.value })}>
              <option value="">Yönetici</option>
              {board.reps.map((r) => (
                <option key={r.rep_id} value={r.rep_id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
        ) : null}
      </div>
      {err ? <div className="banner error">{err}</div> : null}
      <Actions busy={busy} onClose={onClose} onSave={save} onDelete={kr ? remove : undefined} />
    </Modal>
  );
}

function CheckinForm({ kr, onClose, onSaved }: { kr: OkrKeyResult; onClose: () => void; onSaved: () => void }) {
  // Otomatik ölçülen sonuçta değer girilmez; ölçüm yoksa (yedek) gerçekleşen elle girilir.
  const manual = kr.source !== "otomatik";
  const [value, setValue] = useState(manual && kr.current !== null ? String(kr.current) : "");
  const [confidence, setConfidence] = useState(kr.confidence ?? 7);
  const [note, setNote] = useState("");
  const { busy, err, run } = useSave(onSaved);

  function save() {
    void run("/api/okr/checkins", "POST", {
      kr_id: kr.id,
      value: manual && value !== "" ? Number(value) : null,
      confidence,
      note: note.trim() || null,
    });
  }

  return (
    <Modal title="Haftalık check-in" onClose={onClose}>
      <p className="okr-form-note">
        <b>{kr.title}</b>
        <br />
        <span className="muted">
          Şu an {fmtValue(kr.current, kr.unit)} / hedef {fmtValue(kr.target_value, kr.unit)}
          {manual ? "" : " · otomatik ölçülüyor"}
        </span>
      </p>
      {manual ? (
        <Field label={`Güncel değer (${UNIT_LABEL[kr.unit]})`}>
          <input className="f-input num" type="number" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        </Field>
      ) : null}
      <Field label={`Güven: ${confidence}/10`} hint="Dönem sonunda bu sonuca ulaşacağımıza ne kadar inanıyorsun? 5 = yazı tura.">
        <input type="range" min={1} max={10} value={confidence} onChange={(e) => setConfidence(Number(e.target.value))} />
      </Field>
      <Field label="Not" hint="Bu hafta ne oldu, önümüzdeki hafta ne yapacağız, nerede yardım lazım?">
        <textarea className="f-input" rows={3} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {err ? <div className="banner error">{err}</div> : null}
      <Actions busy={busy} onClose={onClose} onSave={save} />
    </Modal>
  );
}

function CloseForm({ objective, onClose, onSaved }: { objective: OkrObjective; onClose: () => void; onSaved: () => void }) {
  const [score, setScore] = useState(String(objective.final_score ?? objective.score ?? ""));
  const [reflection, setReflection] = useState(objective.reflection ?? "");
  const [close, setClose] = useState(objective.status === "kapandi");
  const { busy, err, setErr, run } = useSave(onSaved);

  function save() {
    const value = score === "" ? null : Number(score.replace(",", "."));
    if (value !== null && (Number.isNaN(value) || value < 0 || value > 1)) {
      setErr("Puan 0 ile 1 arasında olmalı.");
      return;
    }
    void run(`/api/okr/objectives/${objective.id}`, "PATCH", {
      ...objectiveBody(objective),
      final_score: value,
      reflection: reflection.trim() || null,
      status: close ? "kapandi" : objective.status === "kapandi" ? "aktif" : objective.status,
    });
  }

  return (
    <Modal title="Puanla ve kapat" onClose={onClose}>
      <p className="okr-form-note">
        <b>{objective.title}</b>
        <br />
        <span className="muted">
          Hesaplanan puan {grade(objective.score)} · {KIND_LABEL[objective.kind]} ({KIND_HINT[objective.kind]})
        </span>
      </p>
      <Field label="Kapanış puanı (0-1)" hint="Hesaplananı onayla ya da bağlamı yansıtacak şekilde düzelt.">
        <input className="f-input num" value={score} onChange={(e) => setScore(e.target.value)} inputMode="decimal" />
      </Field>
      <Field label="Ne öğrendik?" hint="Ne işe yaradı, ne yaramadı, sonraki çeyreğe ne taşıyoruz?">
        <textarea className="f-input" rows={4} value={reflection} onChange={(e) => setReflection(e.target.value)} />
      </Field>
      <label className="check">
        <input type="checkbox" checked={close} onChange={(e) => setClose(e.target.checked)} />
        Hedefi kapat
      </label>
      {err ? <div className="banner error">{err}</div> : null}
      <Actions busy={busy} onClose={onClose} onSave={save} />
    </Modal>
  );
}
