import { Fragment, useEffect, useMemo, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { ShareButton } from "../components/ShareButton";
import { StatTile, ToneBadge } from "../components/StatTile";
import { Modal } from "../finance/ui";
import { ago, duration, int, minutes, num, pct, type Tone } from "../format";
import type {
  ConvItemDetail,
  ConvItemSummary,
  ConvSonarRef,
  ConvStats,
  ConvStatus,
  ConvWeek,
  ConvWeeks,
  Me,
} from "../types";


const TUR_LABEL: Record<string, string> = {
  kesif: "Keşif",
  sunum: "Sunum",
  takip: "Takip",
  kapanis: "Kapanış",
  itiraz: "İtiraz",
  destek: "Destek",
  diger: "Diğer",
};

const ITIRAZ_LABEL: Record<string, string> = {
  fiyat: "Fiyat",
  zaman: "Zaman / sonra",
  guven: "Güven",
  ihtiyac_yok: "İhtiyaç yok",
  rakip: "Rakip",
  karar_verici: "Karar verici başkası",
  odeme: "Ödeme",
  diger: "Diğer",
};

const RISK_LABEL: Record<string, string> = {
  yanlis_bilgi: "Yanlış / abartılı bilgi",
  baski: "Baskıcı satış",
  olmayan_soz: "Verilemeyecek söz",
  uslup: "Üslup",
  diger: "Diğer",
};

const STATUS_LABEL: Record<ConvStatus, string> = {
  bekliyor: "Sırada",
  yazildi: "Yazıya döküldü",
  analiz_edildi: "Analiz edildi",
  hata: "Hata",
  atlandi: "Atlandı",
};

const KARSILANDI: Record<string, { label: string; tone: Tone }> = {
  evet: { label: "Karşılandı", tone: "good" },
  kismen: { label: "Kısmen", tone: "warning" },
  hayir: { label: "Karşılanmadı", tone: "critical" },
};

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const dateTime = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Istanbul",
});

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** İstanbul'a göre bu haftanın pazartesisi (YYYY-AA-GG). */
function thisMonday(): string {
  const s = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
  const d = new Date(`${s}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7));
  return iso(d);
}

function addDays(day: string, n: number): string {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return iso(d);
}

function weekLabel(start: string, current: string): string {
  if (start === current) return "Bu hafta";
  if (start === addDays(current, -7)) return "Geçen hafta";
  return `${dayFmt.format(new Date(`${start}T00:00:00Z`))} haftası`;
}

function scoreTone(v: number | null | undefined): Tone {
  if (v === null || v === undefined) return "neutral";
  if (v >= 4) return "good";
  if (v >= 3) return "neutral";
  if (v >= 2.5) return "warning";
  return "critical";
}

function Score({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  return <ToneBadge tone={scoreTone(v)}>{num(v)}</ToneBadge>;
}

export function ConversationsPage({ me }: { me: Me }) {
  const isAdmin = me.role === "admin";
  const current = thisMonday();
  const [week, setWeek] = useState(() => addDays(current, -7));
  const [picked, setPicked] = useState(false);
  const [repFilter, setRepFilter] = useState<string>("");
  const [openRep, setOpenRep] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const weeks = usePolling<ConvWeeks>("/api/conversations/weeks", 0);
  const { data, error, loading, refresh } = usePolling<ConvWeek>(`/api/conversations?week=${week}`, 0);

  // Varsayılan: geçen hafta (kapanmış); verisi yoksa analizi olan en yeni hafta.
  useEffect(() => {
    const list = weeks.data?.weeks ?? [];
    if (picked || !list.length || list.some((w) => w.week_start === addDays(current, -7) && w.analiz)) return;
    setWeek(list[0].week_start);
  }, [weeks.data, picked, current]);

  const weekOptions = useMemo(() => {
    const set = new Set([current, addDays(current, -7), ...(weeks.data?.weeks ?? []).map((w) => w.week_start)]);
    return [...set].sort().reverse().slice(0, 8);
  }, [current, weeks.data]);

  const items = useMemo(
    () => (data?.items ?? []).filter((i) => !repFilter || i.rep_id === repFilter),
    [data, repFilter],
  );
  const repOptions = useMemo(() => {
    const m = new Map<string, string>();
    for (const i of data?.items ?? []) if (i.rep_id) m.set(i.rep_id, i.rep_name ?? i.rep_id);
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1], "tr"));
  }, [data]);

  const stats: ConvStats | null = isAdmin
    ? repFilter
      ? (data?.reps.find((r) => r.rep_id === repFilter)?.stats ?? null)
      : (data?.team?.stats ?? null)
    : (data?.reps[0]?.stats ?? null);
  const setup = weeks.data?.setup;
  const scoreKeys = Object.keys(data?.score_labels ?? {});
  const pending = data ? data.durum.bekliyor + data.durum.yazildi : 0;

  return (
    <div className="cv-page">
      <div className="page-head">
        <div>
          <h1>Görüşme Analizleri</h1>
          <div className="sub">
            {data
              ? `${dayFmt.format(new Date(`${data.week_start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${data.week_end}T00:00:00Z`))} · telefon görüşmeleri ve toplantılar`
              : "…"}
            {loading ? " · yükleniyor…" : ""}
            {setup?.son_calisma ? ` · son analiz ${ago(setup.son_calisma, new Date())}` : ""}
          </div>
        </div>
        <div className="controls">
          <div className="chips" role="group" aria-label="Hafta">
            {weekOptions.map((w) => (
              <button key={w} className={`chip${week === w ? " on" : ""}`} onClick={() => { setWeek(w); setPicked(true); }} aria-pressed={week === w}>
                {weekLabel(w, current)}
              </button>
            ))}
          </div>
          {isAdmin && repOptions.length ? (
            <select value={repFilter} onChange={(e) => setRepFilter(e.target.value)} aria-label="Temsilci">
              <option value="">Tüm ekip</option>
              {repOptions.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          ) : null}
          <button className="btn" onClick={refresh} disabled={loading}>
            Yenile
          </button>
        </div>
      </div>

      {error ? <div className="banner error">Analizler alınamadı: {error.message}</div> : null}
      {weeks.data && !setup?.son_calisma ? (
        <div className="banner">
          Analiz henüz hiç çalışmadı. Kayıtlar her gece işlenir (telefon: CRM + Verimor, toplantı: Zoho Meeting).
          {isAdmin ? " AssemblyAI ve Claude anahtarları GitHub secrets'ta olunca kayıtlar işlenir." : ""}
        </div>
      ) : null}
      {isAdmin && setup?.rapor ? <RunNotes report={setup.rapor} /> : null}

      {!data ? (
        <div className="tiles">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      ) : (
        <>
          <div className="tiles">
            <StatTile
              label="Analiz edilen görüşme"
              value={int(stats?.analiz ?? 0)}
              expected={stats ? `${int(stats.telefon)} telefon · ${int(stats.toplanti)} toplantı` : "henüz yok"}
              badge={pending ? `${int(pending)} sırada` : undefined}
              tone="neutral"
            />
            <StatTile label="Konuşma süresi" value={minutes(stats?.dakika ?? null)} expected="analiz edilen görüşmeler" />
            <StatTile
              label="Ortalama puan"
              value={num(stats?.puan_ort ?? null)}
              unit="/5"
              expected="beş başlığın ortalaması"
              tone={scoreTone(stats?.puan_ort)}
            />
            <StatTile
              label="Net sonraki adım"
              value={pct(stats?.sonraki_adim_orani ?? null)}
              expected={`tarihli: ${pct(stats?.tarihli_adim_orani ?? null)}`}
              title="Görüşme randevu, ödeme ya da geri arama saati gibi net bir adımla bitti mi"
            />
            <StatTile label="Müşteri ilgisi" value={num(stats?.ilgi_ort ?? null)} unit="/5" expected="satın alma ilgisi" />
            <StatTile
              label="CRM notu"
              value={pct(stats?.crm?.oran ?? null)}
              expected={stats?.crm ? `${int(stats.crm.notlu)} görüşmede not var` : "—"}
              title="Görüşmeden sonraki 3 saat içinde temsilcinin lead'e yazdığı not (SONAR-41 B39, C1-C6)"
              tone={(stats?.crm?.oran ?? 100) < 50 ? "warning" : "neutral"}
            />
          </div>

          {isAdmin && !repFilter && data.team?.narrative ? <TeamCard n={data.team.narrative} /> : null}

          {data.reps.length ? (
            <section className="card" data-share="gorusme-temsilciler">
              <div className="card-head">
                <h2>{isAdmin ? "Temsilciler" : "Haftam"}</h2>
                <span className="hint">
                  SONAR-41 boyutları, 1-5<span data-share-skip> · satıra tıkla: haftanın odak maddesi</span>
                </span>
                {isAdmin && !repFilter ? (
                  <ShareButton
                    kart="gorusme-temsilciler"
                    baslik={`Görüşme Analizleri · ${dayFmt.format(new Date(`${data.week_start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${data.week_end}T00:00:00Z`))}`}
                  />
                ) : null}
              </div>
              <div className="card-body">
                <div className="table-wrap">
                  <table className="board">
                    <thead>
                      <tr>
                        <th>Temsilci</th>
                        <th className="num">Görüşme</th>
                        <th className="num">Süre</th>
                        {scoreKeys.map((k) => (
                          <th key={k} className="num" title={data.score_labels[k]}>
                            {k}
                          </th>
                        ))}
                        <th className="num">Sonraki adım</th>
                        <th className="num" title="Not yazılan görüşme oranı">CRM notu</th>
                        <th>Odak</th>
                        {isAdmin ? <th data-share-skip>Cliq</th> : null}
                      </tr>
                    </thead>
                    <tbody>
                      {data.reps
                        .filter((r) => !repFilter || r.rep_id === repFilter)
                        .map((r) => {
                          const isOpen = openRep === r.rep_id || !isAdmin;
                          return (
                            <Fragment key={r.rep_id}>
                              <tr
                                className="clickable"
                                onClick={() => setOpenRep(isOpen ? null : r.rep_id)}
                                aria-expanded={isOpen}
                              >
                                <td>
                                  <div className="name">
                                    <span className="caret" aria-hidden data-share-skip>
                                      {isOpen ? "▾" : "▸"}
                                    </span>{" "}
                                    {r.name}
                                  </div>
                                </td>
                                <td className="num">{int(r.stats.analiz)}</td>
                                <td className="num">{minutes(r.stats.dakika)}</td>
                                {scoreKeys.map((k) => (
                                  <td key={k} className="num">
                                    <Score v={r.stats.puanlar[k]} />
                                  </td>
                                ))}
                                <td className="num">{pct(r.stats.sonraki_adim_orani)}</td>
                                <td className="num">{pct(r.stats.crm?.oran ?? null)}</td>
                                <td className="cv-wrap">
                                  {r.narrative ? (
                                    <>
                                      {r.narrative.odak.kod ? <span className="cv-code">{r.narrative.odak.kod}</span> : null}
                                      {r.narrative.odak.baslik}
                                    </>
                                  ) : (
                                    <span className="muted">—</span>
                                  )}
                                </td>
                                {isAdmin ? (
                                  <td className="cv-share-cell" data-share-skip onClick={(e) => e.stopPropagation()}>
                                    {r.narrative ? (
                                      <ShareToCliq
                                        week={data.week_start}
                                        repId={r.rep_id}
                                        name={r.name}
                                        sentAt={r.sent_at}
                                        onSent={refresh}
                                        compact
                                      />
                                    ) : (
                                      <span className="muted">—</span>
                                    )}
                                  </td>
                                ) : null}
                              </tr>
                              {isOpen && r.narrative ? (
                                <tr className="cv-detail-row" data-share-skip>
                                  <td colSpan={scoreKeys.length + (isAdmin ? 7 : 6)}>
                                    <RepNarrative n={r.narrative} />
                                    {isAdmin ? (
                                      <ShareToCliq
                                        week={data.week_start}
                                        repId={r.rep_id}
                                        name={r.name}
                                        sentAt={r.sent_at}
                                        onSent={refresh}
                                      />
                                    ) : null}
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
                <p className="muted cv-legend">
                  {scoreKeys.map((k) => `${k} ${data.score_labels[k]}`).join(" · ")}
                </p>
              </div>
            </section>
          ) : null}

          {stats && Object.keys(stats.davranislar ?? {}).length ? (
            <section className="card">
              <div className="card-head">
                <h2>Davranış haritası</h2>
                <span className="hint">SONAR-41 · en düşük ortalamalı davranışlar üstte · 2 ve altı = bug</span>
              </div>
              <div className="card-body">
                <BehaviorMap stats={stats} sonar={data.sonar} labels={data.score_labels} />
              </div>
            </section>
          ) : null}

          {stats && Object.keys(stats.itirazlar).length ? (
            <section className="card">
              <div className="card-head">
                <h2>İtirazlar</h2>
                <span className="hint">müşterinin dile getirdiği itirazlar ve karşılanma oranı (kısmen = yarım)</span>
              </div>
              <div className="card-body">
                <div className="table-wrap">
                  <table className="board">
                    <thead>
                      <tr>
                        <th>İtiraz</th>
                        <th className="num">Adet</th>
                        <th className="num">Karşılandı</th>
                        <th className="num">Kısmen</th>
                        <th className="num">Karşılanma</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(stats.itirazlar).map(([tip, o]) => (
                        <tr key={tip}>
                          <td>{ITIRAZ_LABEL[tip] ?? tip}</td>
                          <td className="num">{int(o.adet)}</td>
                          <td className="num">{int(o.karsilandi)}</td>
                          <td className="num">{int(o.kismen)}</td>
                          <td className="num">
                            <ToneBadge tone={o.oran >= 70 ? "good" : o.oran >= 40 ? "warning" : "critical"}>
                              {pct(o.oran)}
                            </ToneBadge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {Object.keys(stats.riskler).length ? (
                  <p className="cv-risk">
                    <strong>Riskli anlar:</strong>{" "}
                    {Object.entries(stats.riskler)
                      .map(([k, v]) => `${RISK_LABEL[k] ?? k} (${v})`)
                      .join(" · ")}
                  </p>
                ) : null}
              </div>
            </section>
          ) : null}

          <section className="card">
            <div className="card-head">
              <h2>Görüşmeler</h2>
              <span className="hint">
                {int(items.length)} görüşme · 1 dk altı çağrılar alınmaz · ayrıntı için tıkla
              </span>
            </div>
            <div className="card-body">
              {items.length ? (
                <ItemTable items={items} showRep={isAdmin && !repFilter} onOpen={setDetailId} />
              ) : (
                <div className="empty">Bu hafta için kayıt yok.</div>
              )}
            </div>
          </section>
        </>
      )}

      {detailId !== null && data ? (
        <ItemDetail id={detailId} sonar={data.sonar} labels={data.score_labels} onClose={() => setDetailId(null)} />
      ) : null}
    </div>
  );
}

function RunNotes({ report }: { report: Record<string, unknown> }) {
  // Son çalışmada atlanan veya hata veren adımlar (ör. anahtar yok, toplantı yetkisi yok).
  const notes: string[] = [];
  const walk = (label: string, v: unknown) => {
    if (!v || typeof v !== "object") return;
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      if ((k === "atlandi" || k === "hata") && typeof val === "string") notes.push(`${label}: ${val}`);
      else if (val && typeof val === "object") walk(k === "toplanti" ? "Toplantı" : k === "telefon" ? "Telefon" : label, val);
    }
  };
  walk("Analiz", report);
  if (!notes.length) return null;
  return (
    <div className="banner">
      <strong>Son çalışmada eksik kalan:</strong>
      <ul className="cv-list">
        {notes.map((n) => (
          <li key={n}>{n}</li>
        ))}
      </ul>
    </div>
  );
}

function TeamCard({ n }: { n: NonNullable<NonNullable<ConvWeek["team"]>["narrative"]> }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Haftanın özeti</h2>
        <span className="hint">yapay zekâ yorumu · sayılar koddan</span>
      </div>
      <div className="card-body cv-team">
        <p className="cv-lead">{n.ozet}</p>
        <div className="cv-cols">
          <div>
            <h3>Müşterinin sesi</h3>
            <ul className="cv-list">
              {n.musteri_sesi.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3>İtirazlar</h3>
            <p>{n.itirazlar}</p>
            {n.iyi_ornekler.length ? (
              <>
                <h3>İyi örnekler</h3>
                <ul className="cv-list">
                  {n.iyi_ornekler.map((s) => (
                    <li key={s}>{s}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
          <div>
            <h3>Bu hafta yapılacaklar</h3>
            <ul className="cv-list">
              {n.aksiyonlar.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

function RepNarrative({ n }: { n: NonNullable<ConvWeek["reps"][number]["narrative"]> }) {
  return (
    <div className="cv-rep">
      <p className="cv-lead">{n.ozet}</p>
      <div className="cv-focus">
        <div className="label">Odak: {n.odak.baslik}</div>
        <p>{n.odak.neden}</p>
        {n.odak.alinti ? <blockquote className="cv-quote">“{n.odak.alinti}”</blockquote> : null}
        <p>
          <strong>Bir dahaki görüşmede:</strong> {n.odak.nasil}
        </p>
      </div>
      <div className="cv-cols">
        <div>
          <h3>Güçlü yönler</h3>
          <ul className="cv-list">
            {n.guclu.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Dikkat</h3>
          <ul className="cv-list">
            {n.dikkat.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}

function ShareToCliq({
  week,
  repId,
  name,
  sentAt,
  onSent,
  compact = false,
}: {
  week: string;
  repId: string;
  name: string;
  sentAt: string | null;
  onSent: () => void;
  /** Tablo satırında: kısa etiket, gönderim tarihi altta */
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function send() {
    const again = sentAt ? " Bu hafta zaten gönderildi; yeniden gönderilsin mi?" : "";
    if (!window.confirm(`${name} için haftalık değerlendirme Cliq'ten yalnız kendisine gönderilecek.${again}`)) return;
    setBusy(true);
    setErr(null);
    try {
      await api("/api/conversations/share", { method: "POST", body: JSON.stringify({ week, rep_id: repId }) });
      onSent();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  if (compact) {
    return (
      <div className="cv-share compact">
        <button className="btn sm" onClick={send} disabled={busy} title={err ?? undefined}>
          {busy ? "Gönderiliyor…" : sentAt ? "Yeniden gönder" : "Gönder"}
        </button>
        {err ? (
          <span className="warn-text">Gönderilemedi</span>
        ) : sentAt ? (
          <span className="muted">✓ {dateTime.format(new Date(sentAt))}</span>
        ) : null}
      </div>
    );
  }
  return (
    <div className="cv-share">
      <button className="btn" onClick={send} disabled={busy}>
        {busy ? "Gönderiliyor…" : sentAt ? "Cliq'e yeniden gönder" : "Cliq'e gönder"}
      </button>
      <span className="muted">
        {sentAt ? `${dateTime.format(new Date(sentAt))} gönderildi · ` : ""}yalnız {name.split(" ")[0]} görür
      </span>
      {err ? <span className="warn-text">{err}</span> : null}
    </div>
  );
}

function ItemTable({
  items,
  showRep,
  onOpen,
}: {
  items: ConvItemSummary[];
  showRep: boolean;
  onOpen: (id: number) => void;
}) {
  return (
    <div className="table-wrap">
      <table className="board">
        <thead>
          <tr>
            <th>Zaman</th>
            {showRep ? <th>Temsilci</th> : null}
            <th>Müşteri</th>
            <th>Görüşme</th>
            <th className="num">Süre</th>
            <th className="num">Puan</th>
            <th>Sonraki adım</th>
            <th>İtiraz</th>
            <th>Özet</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr key={i.id} className="clickable" onClick={() => onOpen(i.id)}>
              <td>{dateTime.format(new Date(i.occurred_at))}</td>
              {showRep ? <td>{i.rep_name ?? "—"}</td> : null}
              <td>{i.customer ?? "—"}</td>
              <td>
                {i.kind === "call" ? "Telefon" : "Toplantı"}
                {i.tur ? <span className="muted"> · {TUR_LABEL[i.tur] ?? i.tur}</span> : null}
              </td>
              <td className="num">{duration(i.duration_sec)}</td>
              <td className="num">
                {i.status === "analiz_edildi" ? <Score v={i.puan} /> : <span className="muted">—</span>}
              </td>
              <td>
                {i.status !== "analiz_edildi" || i.analiz_edilebilir === false ? (
                  <span className="muted">—</span>
                ) : i.sonraki_adim ? (
                  <ToneBadge tone="good">Var</ToneBadge>
                ) : (
                  <ToneBadge tone="warning">Yok</ToneBadge>
                )}
              </td>
              <td>{i.itirazlar.map((t) => ITIRAZ_LABEL[t] ?? t).join(", ") || <span className="muted">—</span>}</td>
              <td className="cv-wrap cv-summary">
                {i.status === "analiz_edildi" ? (
                  i.ozet
                ) : (
                  <span className={i.status === "hata" ? "warn-text" : "muted"} title={i.error ?? undefined}>
                    {STATUS_LABEL[i.status]}
                    {i.status === "atlandi" || i.status === "hata" ? (i.error ? ` · ${i.error}` : "") : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ItemDetail({
  id,
  sonar,
  labels,
  onClose,
}: {
  id: number;
  sonar: ConvSonarRef;
  labels: Record<string, string>;
  onClose: () => void;
}) {
  const [row, setRow] = useState<ConvItemDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);
  useEffect(() => {
    api<ConvItemDetail>(`/api/conversations/item/${id}`)
      .then(setRow)
      .catch((e: unknown) => setErr(e instanceof ApiError ? e.message : String(e)));
  }, [id]);
  const a = row?.analysis;
  const title = row
    ? `${row.kind === "call" ? "Telefon" : "Toplantı"} · ${row.customer ?? "müşteri"} · ${row.rep_name ?? ""}`
    : "Görüşme";
  return (
    <Modal title={title} onClose={onClose} wide>
      {err ? <div className="banner error">{err}</div> : null}
      {!row ? (
        !err ? <div className="skeleton" /> : null
      ) : (
        <div className="cv-detail">
          <div className="muted">
            {dateTime.format(new Date(row.occurred_at))} · {duration(row.duration_sec)} · CRM sonucu:{" "}
            {row.crm_outcome ?? "girilmemiş"}
          </div>
          {!a ? (
            <p className="muted">{STATUS_LABEL[row.status]}{row.error ? ` · ${row.error}` : ""}</p>
          ) : !a.analiz_edilebilir ? (
            <p>Analiz edilmedi: {a.neden}</p>
          ) : (
            <>
              <p className="cv-lead">{a.ozet}</p>
              <SonarBlock rows={a.sonar ?? []} sonar={sonar} labels={labels} />
              <p>
                <strong>Sonraki adım:</strong>{" "}
                {a.sonraki_adim.var ? a.sonraki_adim.aciklama : <span className="warn-text">net adım yok</span>}
                {a.sonraki_adim.var && !a.sonraki_adim.tarih_net ? <span className="muted"> (tarih konuşulmadı)</span> : null}
              </p>
              <p>
                <strong>Müşteri:</strong> ilgi {a.musteri.ilgi}/5 · {a.musteri.duygu}
                {a.musteri.ihtiyaclar.length ? ` · ${a.musteri.ihtiyaclar.join("; ")}` : ""}
              </p>
              {a.itirazlar.length ? (
                <>
                  <h3>İtirazlar</h3>
                  <ul className="cv-list">
                    {a.itirazlar.map((o, n) => (
                      <li key={n}>
                        {ITIRAZ_LABEL[o.tip] ?? o.tip}{" "}
                        <ToneBadge tone={KARSILANDI[o.karsilandi]?.tone ?? "neutral"}>
                          {KARSILANDI[o.karsilandi]?.label ?? o.karsilandi}
                        </ToneBadge>
                        <blockquote className="cv-quote">“{o.alinti}”</blockquote>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              <div className="cv-cols">
                <Quoted title="Güçlü" rows={a.guclu} sonar={sonar} />
                <Quoted title="Gelişim" rows={a.gelisim} sonar={sonar} />
              </div>
              <CrmBlock crm={a.crm} notes={row.notes} sonar={sonar} />
              {a.riskler.length ? (
                <>
                  <h3>Riskli anlar</h3>
                  <ul className="cv-list">
                    {a.riskler.map((r, n) => (
                      <li key={n}>
                        <span className="warn-text">{RISK_LABEL[r.tip] ?? r.tip}:</span> {r.aciklama}
                        <blockquote className="cv-quote">“{r.alinti}”</blockquote>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              {a.sozler.length ? (
                <>
                  <h3>Verilen sözler</h3>
                  <ul className="cv-list">
                    {a.sozler.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ul>
                </>
              ) : null}
            </>
          )}
          {row.transcript ? (
            <div>
              <button className="btn" onClick={() => setShowTranscript((v) => !v)}>
                {showTranscript ? "Transkripti gizle" : "Transkripti göster"}
              </button>
              {showTranscript ? (
                <pre className="cv-transcript">
                  {a?.temsilci_konusmaci && a.temsilci_konusmaci !== "belirsiz"
                    ? `(${a.temsilci_konusmaci} = temsilci)\n\n`
                    : ""}
                  {row.transcript}
                </pre>
              ) : null}
            </div>
          ) : null}
          <p className="muted cv-foot">
            Otomatik transkript ve yapay zekâ analizi ({row.ai_model ?? "—"}); yanlış duyulmuş kelimeler olabilir.
          </p>
        </div>
      )}
    </Modal>
  );
}

function BehaviorMap({
  stats,
  sonar,
  labels,
}: {
  stats: ConvStats;
  sonar: ConvSonarRef;
  labels: Record<string, string>;
}) {
  const [all, setAll] = useState(false);
  const rows = Object.entries(stats.davranislar)
    .map(([kod, r]) => ({ kod, ...r, ...(sonar.behaviors[kod] ?? { dim: "", name: kod }) }))
    .sort((x, y) => (x.ort ?? 9) - (y.ort ?? 9) || y.adet - x.adet);
  const shown = all ? rows : rows.slice(0, 10);
  return (
    <>
      <div className="table-wrap">
        <table className="board">
          <thead>
            <tr>
              <th>Davranış</th>
              <th>Boyut</th>
              <th className="num">Ortalama</th>
              <th className="num" title="Bu davranışın fırsatı doğan görüşme sayısı">Gözlem</th>
              <th className="num" title="2 ve altı puan alan görüşme">Bug</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.kod}>
                <td className="cv-wrap">
                  <span className="cv-code">{r.kod}</span>
                  {r.name}
                </td>
                <td className="muted">{labels[r.dim] ?? r.dim}</td>
                <td className="num">
                  <Score v={r.ort} />
                </td>
                <td className="num">{int(r.adet)}</td>
                <td className="num">{r.zayif ? int(r.zayif) : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 10 ? (
        <button className="btn cv-more" onClick={() => setAll((v) => !v)}>
          {all ? "İlk 10'u göster" : `Tümünü göster (${rows.length})`}
        </button>
      ) : null}
    </>
  );
}

function SonarBlock({
  rows,
  sonar,
  labels,
}: {
  rows: { kod: string; puan: number; alinti: string }[];
  sonar: ConvSonarRef;
  labels: Record<string, string>;
}) {
  if (!rows.length) return null;
  const byDim = new Map<string, typeof rows>();
  for (const r of rows) {
    const dim = sonar.behaviors[r.kod]?.dim ?? "?";
    byDim.set(dim, [...(byDim.get(dim) ?? []), r]);
  }
  return (
    <div className="cv-sonar">
      {[...byDim.entries()]
        .sort(([x], [y]) => x.localeCompare(y))
        .map(([dim, list]) => {
          const avg = list.reduce((s, r) => s + r.puan, 0) / list.length;
          return (
            <div key={dim} className="cv-dim">
              <div className="cv-dim-head">
                <span>
                  {dim} · {labels[dim] ?? ""}
                </span>
                <Score v={Math.round(avg * 10) / 10} />
              </div>
              <ul className="cv-list">
                {list.map((r) => (
                  <li key={r.kod}>
                    <span className="cv-code">{r.kod}</span>
                    {sonar.behaviors[r.kod]?.name ?? r.kod} <Score v={r.puan} />
                    {r.alinti ? <blockquote className="cv-quote">“{r.alinti}”</blockquote> : null}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
    </div>
  );
}

function CrmBlock({
  crm,
  notes,
  sonar,
}: {
  crm?: { not_var: boolean; puanlar: Record<string, number>; yorum: string };
  notes?: { bu: string[]; onceki: string[] };
  sonar: ConvSonarRef;
}) {
  const [history, setHistory] = useState(false);
  if (!crm && !notes) return null;
  return (
    <div>
      <h3>CRM notu</h3>
      {notes?.bu.length ? (
        <ul className="cv-list">
          {notes.bu.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : (
        <p className="warn-text">Bu görüşme için not yazılmamış.</p>
      )}
      {crm?.not_var ? (
        <div className="cv-scores">
          {Object.entries(sonar.crm).map(([k, name]) => (
            <div key={k} className="cv-score" title={name}>
              <span>
                {k} {name}
              </span>
              {crm.puanlar[k] > 0 ? <Score v={crm.puanlar[k]} /> : <span className="muted">—</span>}
            </div>
          ))}
        </div>
      ) : null}
      {crm?.yorum ? <p className="muted">{crm.yorum}</p> : null}
      {notes?.onceki.length ? (
        <>
          <button className="link-btn" onClick={() => setHistory((v) => !v)}>
            {history ? "Önceki notları gizle" : `Önceki notlar (${notes.onceki.length})`}
          </button>
          {history ? (
            <ul className="cv-list muted">
              {notes.onceki.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function Quoted({
  title,
  rows,
  sonar,
}: {
  title: string;
  rows: { kod?: string; madde: string; alinti: string }[];
  sonar: ConvSonarRef;
}) {
  if (!rows.length) return <div />;
  return (
    <div>
      <h3>{title}</h3>
      <ul className="cv-list">
        {rows.map((r, n) => (
          <li key={n}>
            {r.kod ? (
              <span className="cv-code" title={sonar.behaviors[r.kod]?.name}>
                {r.kod}
              </span>
            ) : null}
            {r.madde}
            <blockquote className="cv-quote">“{r.alinti}”</blockquote>
          </li>
        ))}
      </ul>
    </div>
  );
}
