import { Fragment, useEffect, useMemo, useState } from "react";

import { api, ApiError, usePolling } from "../api";
import { ShareButton } from "../components/ShareButton";
import { StatTile, ToneBadge } from "../components/StatTile";
import { Modal } from "../finance/ui";
import { ago, duration, int, minutes, num, pct, type Tone } from "../format";
import type {
  ConvRequest,
  ConvBug,
  ConvExample,
  ConvItemDetail,
  ConvItemSummary,
  ConvSonarRef,
  ConvStats,
  ConvStatus,
  ConvWeek,
  ConvWeeks,
  Me,
} from "../types";


const HOLD_LABEL: Record<string, string> = {
  tuttu: "müşteriyi tuttu",
  kismen: "kısmen tuttu",
  birakti: "sorup bıraktı",
  uygulanmaz: "—",
};

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

/** Haftanın tarih aralığı (pazartesi–pazar); bu / geçen hafta ayrıca belirtilir. */
function weekLabel(start: string, current: string): string {
  const range = `${dayFmt.format(new Date(`${start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${addDays(start, 6)}T00:00:00Z`))}`;
  if (start === current) return `${range} · bu hafta`;
  if (start === addDays(current, -7)) return `${range} · geçen hafta`;
  return range;
}

function scoreTone(v: number | null | undefined): Tone {
  if (v === null || v === undefined) return "neutral";
  if (v >= 4) return "good";
  // Puanlama tablosunun basamakları: 4 uyarlanmış · 3 doğru · 2 denendi · 1 kaçırıldı.
  if (v >= 3) return "neutral";
  if (v >= 2) return "warning";
  return "critical";
}

// Ton ikonu (▲ ▼ –) burada artış/azalış ya da eksi gibi okunuyor; puan yalnız renkle verilir.
function Score({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">—</span>;
  return <span className={`badge ${scoreTone(v)}`}>{num(v)}</span>;
}

/** Ortalama + dağılım: aynı 3 "hep yeterli" de olabilir "bazen iyi bazen zayıf" da. */
function SpreadScore({ v, spread }: { v: number | null | undefined; spread?: number[] }) {
  const weak = spread ? spread[0] + spread[1] : 0;
  const strong = spread ? spread[3] + spread[4] : 0;
  const title = spread?.some((n) => n) ? spread.map((n, i) => `${i + 1}: ${n}`).join(" · ") + " davranış puanı" : undefined;
  return (
    <span className="cv-spread" title={title}>
      <Score v={v} />
      {weak || strong ? (
        <small>
          {weak ? <span className="weak">{weak} zayıf</span> : null}
          {weak && strong ? " · " : null}
          {strong ? <span className="strong">{strong} güçlü</span> : null}
        </small>
      ) : null}
    </span>
  );
}

export function ConversationsPage({ me }: { me: Me }) {
  const isAdmin = me.role === "admin";
  const current = thisMonday();
  // Açılışta bu hafta; geçmiş haftalar listeden seçilir.
  const [week, setWeek] = useState(current);
  const [repFilter, setRepFilter] = useState<string>("");
  const [openRep, setOpenRep] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [guide, setGuide] = useState(false);
  const [listOpen, setListOpen] = useState(false);
  useEffect(() => setListOpen(false), [week]);
  const weeks = usePolling<ConvWeeks>("/api/conversations/weeks", 0);
  const { data, error, loading, refresh } = usePolling<ConvWeek>(`/api/conversations?week=${week}`, 0);

  const weekOptions = useMemo(() => {
    const set = new Set([current, addDays(current, -7), ...(weeks.data?.weeks ?? []).map((w) => w.week_start)]);
    return [...set].sort().reverse().slice(0, 12);
  }, [current, weeks.data]);
  const analysedIn = useMemo(
    () => new Map((weeks.data?.weeks ?? []).map((w) => [w.week_start, w.analiz])),
    [weeks.data],
  );

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
          <select className="f-input" value={week} onChange={(e) => setWeek(e.target.value)} aria-label="Hafta">
            {weekOptions.map((w) => (
              <option key={w} value={w}>
                {weekLabel(w, current)}
                {analysedIn.get(w) ? ` · ${analysedIn.get(w)} analiz` : ""}
              </option>
            ))}
          </select>
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
          <button className="btn" onClick={() => setGuide((g) => !g)} aria-expanded={guide}>
            Nasıl çalışır
          </button>
          <button className="btn" onClick={refresh} disabled={loading}>
            Yenile
          </button>
        </div>
      </div>

      {guide ? <SonarGuide labels={data?.score_labels ?? {}} isAdmin={isAdmin} /> : null}
      {error ? <div className="banner error">Analizler alınamadı: {error.message}</div> : null}
      {data && !data.durum.analiz_edildi ? (
        <EmptyWeek
          week={week}
          current={current}
          waiting={data.durum.bekliyor + data.durum.yazildi}
          latest={(weeks.data?.weeks ?? []).find((w) => w.analiz > 0)?.week_start ?? null}
          onPick={setWeek}
        />
      ) : null}
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
            {stats?.musaitlik?.temsilci_sordu ? (
              <StatTile
                label="“Müsait misiniz?”"
                value={int(stats.musaitlik.temsilci_sordu)}
                unit="görüşmede"
                expected={`analiz edilen ${int(stats.analiz)} görüşmeden`}
                title="Temsilcinin 'müsait misiniz / uygun musunuz' diye sorup müşteriye çıkış kapısı açtığı görüşmeler (B01 hatası)"
                tone="warning"
              />
            ) : null}
            {stats?.musaitlik?.adet ? (
              <StatTile
                label="Müsait değil anı"
                value={`${int(stats.musaitlik.tuttu)} / ${int(stats.musaitlik.adet)}`}
                unit="tuttu"
                expected={`kısmen ${int(stats.musaitlik.kismen)} · bıraktı ${int(stats.musaitlik.birakti)} · net geri arama ${int(stats.musaitlik.net_geri_arama)}`}
                title="Müşteri müsait olmadığını söylediğinde temsilci ilgiyi canlı tuttu mu, yoksa 'sonra arayayım' deyip bıraktı mı"
                tone={stats.musaitlik.birakti > stats.musaitlik.tuttu ? "warning" : "neutral"}
              />
            ) : null}
            {isAdmin && data.maliyet ? (
              <StatTile
                label="Yapay zekâ maliyeti"
                value={`$${(data.maliyet.analiz_usd + data.maliyet.haftalik_usd).toLocaleString("tr-TR", { maximumFractionDigits: 2 })}`}
                expected={`${int(data.maliyet.analiz_adet)} analiz${data.durum.kota_disi ? ` · ${int(data.durum.kota_disi)} kayıt kota dışı` : ""}`}
                title="Bu haftanın görüşme analizleri ve haftalık yorumları; toplu gönderim yarı fiyat"
              />
            ) : null}
          </div>

          {isAdmin && !repFilter && data.team?.narrative ? (
            <TeamCard
              n={data.team.narrative}
              weekText={`${dayFmt.format(new Date(`${data.week_start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${data.week_end}T00:00:00Z`))}`}
            />
          ) : null}

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
                                    <SpreadScore v={r.stats.puanlar[k]} spread={r.dagilim?.[k]} />
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
                  <br />
                  Puanın altındaki sayılar o boyutta 1-2 (zayıf) ve 4-5 (güçlü) alan davranış sayısıdır; ikisi birlikte
                  yüksekse temsilci bunu yapabiliyor ama tutarsız.
                </p>
              </div>
            </section>
          ) : null}

          {data.ornek_temsilci?.length ? (
            <ExampleCard
              rows={data.ornek_temsilci}
              sonar={data.sonar}
              labels={data.score_labels}
              isAdmin={isAdmin}
              scope={repFilter ? (repOptions.find(([id]) => id === repFilter)?.[1] ?? "") : ""}
              weekText={`${dayFmt.format(new Date(`${data.week_start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${data.week_end}T00:00:00Z`))}`}
              onOpen={setDetailId}
            />
          ) : null}

          {data.bugs?.length ? (
            <BugCard
              bugs={data.bugs}
              sonar={data.sonar}
              labels={data.score_labels}
              reps={repOptions}
              isAdmin={isAdmin}
              pageRep={repFilter}
              weekText={`${dayFmt.format(new Date(`${data.week_start}T00:00:00Z`))} – ${dayFmt.format(new Date(`${data.week_end}T00:00:00Z`))}`}
              onOpen={setDetailId}
            />
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
            <div
              className="card-head clickable"
              role="button"
              tabIndex={0}
              aria-expanded={listOpen}
              onClick={() => setListOpen((v) => !v)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setListOpen((v) => !v)}
            >
              <h2>
                <span className="caret" aria-hidden>
                  {listOpen ? "▾" : "▸"}
                </span>{" "}
                Görüşmeler
              </h2>
              <span className="hint">
                {int(items.length)} görüşme · 1 dk altı çağrılar alınmaz ·{" "}
                {listOpen ? "ayrıntı için satıra tıkla" : "listeyi açmak için tıkla"}
              </span>
            </div>
            {listOpen ? (
              <div className="card-body">
                {items.length ? (
                  <ItemTable items={items} showRep={isAdmin && !repFilter} onOpen={setDetailId} />
                ) : (
                  <div className="empty">Bu hafta için kayıt yok.</div>
                )}
              </div>
            ) : null}
          </section>

          {isAdmin ? <RequestCard onOpen={setDetailId} /> : null}
        </>
      )}

      {detailId !== null && data ? (
        <ItemDetail id={detailId} sonar={data.sonar} labels={data.score_labels} onClose={() => setDetailId(null)} />
      ) : null}
    </div>
  );
}

const callDayFmt = new Intl.DateTimeFormat("tr-TR", { weekday: "short", day: "numeric", month: "short", timeZone: "Europe/Istanbul" });

/** Örnek görüşmeler: haftanın en iyi görüşmelerinde ne yapıldığı, güçlü anlar K1→K5 sırasıyla.
 * Her görüşme kanala ayrı gönderilir. */
function ExampleCard({
  rows,
  sonar,
  labels,
  isAdmin,
  scope,
  weekText,
  onOpen,
}: {
  rows: { rep_id: string; rep_name: string; ornek: ConvExample | null }[];
  sonar: ConvSonarRef;
  labels: Record<string, string>;
  isAdmin: boolean;
  scope: string;
  weekText: string;
  onOpen: (id: number) => void;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const found = rows.filter((r) => r.ornek).length;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Örnek görüşmeler{scope ? ` · ${scope}` : ""}</h2>
        <span className="hint">
          {weekText} · her temsilcinin haftadaki en iyi görüşmesi ({found}/{rows.length}) · satıra tıkla: 4-5 alan anlar
        </span>
      </div>
      <div className="card-body">
        {rows.map(({ rep_id, rep_name, ornek: e }) => {
          const isOpen = !!e && open.has(rep_id);
          return (
            <article key={rep_id} className={`cv-example${isOpen ? " open" : ""}`} data-share={e ? `gorusme-ornek-${e.item_id}` : undefined}>
              <div
                className={`cv-example-head${e ? " clickable" : ""}`}
                role={e ? "button" : undefined}
                tabIndex={e ? 0 : undefined}
                aria-expanded={e ? isOpen : undefined}
                onClick={() => e && toggle(rep_id)}
                onKeyDown={(ev) => e && (ev.key === "Enter" || ev.key === " ") && toggle(rep_id)}
              >
                <span className="caret" aria-hidden data-share-skip>
                  {e ? (isOpen ? "▾" : "▸") : "·"}
                </span>
                <div className="cv-example-title">
                  <b>{rep_name}</b>
                  {e ? (
                    <span className="muted">
                      {" "}
                      · {e.kind === "call" ? "telefon" : "toplantı"}
                      {e.tur ? ` · ${TUR_LABEL[e.tur] ?? e.tur}` : ""} · {minutes((e.duration_sec ?? 0) / 60)} ·{" "}
                      {callDayFmt.format(new Date(e.occurred_at))}
                      {e.crm_outcome ? ` · CRM: ${e.crm_outcome}` : ""}
                    </span>
                  ) : (
                    <span className="muted"> · bu hafta örnek görüşme yok</span>
                  )}
                </div>
                {e ? <Score v={e.puan} /> : null}
                {e && isOpen && isAdmin ? (
                  <span onClick={(ev) => ev.stopPropagation()}>
                    <ShareButton kart={`gorusme-ornek-${e.item_id}`} baslik={`Örnek görüşme · ${rep_name} · ${weekText}`} />
                  </span>
                ) : null}
              </div>
              {e && isOpen ? (
                <>
                  {e.ozet ? <p className="cv-example-sum">{e.ozet}</p> : null}
                  <ol className="cv-example-moments">
                    {e.anlar.map((m) => {
                      const b = sonar.behaviors[m.kod];
                      return (
                        <li key={m.kod}>
                          <div>
                            <span className="cv-code">{m.kod}</span>
                            <b>{b?.name ?? m.kod}</b>
                            <span className="muted">
                              {" "}
                              · {b?.dim} {b?.dim ? labels[b.dim] : ""}
                            </span>{" "}
                            <Score v={m.puan} />
                          </div>
                          {m.alinti ? <blockquote className="cv-quote">“{m.alinti}”</blockquote> : null}
                        </li>
                      );
                    })}
                  </ol>
                  {e.sonraki_adim ? (
                    <p className="cv-example-next">
                      <span className="label">Sonraki adım</span> {e.sonraki_adim}
                    </p>
                  ) : null}
                  <button className="link-btn" onClick={() => onOpen(e.item_id)}>
                    görüşmeyi aç
                  </button>
                </>
              ) : null}
            </article>
          );
        })}
        <p className="muted cv-legend">
          Örnek görüşme: en az 8 davranışı puanlanmış, en az 3'ü 4-5 alan görüşmelerden ortalaması en yüksek olan.
        </p>
      </div>
    </section>
  );
}

const BUGS_PER_DIM = 3;

/** Haftanın bug'ları: SONAR kodu, standart, ihlal anı ve doğrusu; K1'den K5'e görüşme
 * sırasıyla. Ekip görünümünde isim yok; kanala paylaşılmak üzere tasarlandı. */
function BugCard({
  bugs,
  sonar,
  labels,
  reps,
  isAdmin,
  pageRep,
  weekText,
  onOpen,
}: {
  bugs: ConvBug[];
  sonar: ConvSonarRef;
  labels: Record<string, string>;
  reps: [string, string][];
  isAdmin: boolean;
  pageRep: string;
  weekText: string;
  onOpen: (id: number) => void;
}) {
  const [rep, setRep] = useState(pageRep);
  const [all, setAll] = useState(false);
  useEffect(() => setRep(pageRep), [pageRep]);

  const dims = useMemo(() => {
    const m = new Map<string, ConvBug[]>();
    for (const b of bugs) if (!rep || b.rep_id === rep) m.set(b.kod, [...(m.get(b.kod) ?? []), b]);
    const groups = [...m.entries()].map(([kod, list]) => ({
      kod,
      dim: sonar.behaviors[kod]?.dim ?? "?",
      list,
      reps: new Set(list.map((b) => b.rep_id)).size,
      // Doğrusu yazılmış ve en düşük puanlı örnekler önce.
      examples: [...list].sort((x, y) => Number(!x.dogrusu) - Number(!y.dogrusu) || x.puan - y.puan),
    }));
    return Object.keys(labels)
      .map((dim) => {
        const codes = groups
          .filter((g) => g.dim === dim)
          .sort((x, y) => y.list.length - x.list.length || x.kod.localeCompare(y.kod));
        return { dim, codes, total: codes.reduce((s, g) => s + g.list.length, 0) };
      })
      .filter((d) => d.codes.length);
  }, [bugs, rep, sonar, labels]);

  const repName = rep ? (reps.find(([id]) => id === rep)?.[1] ?? "") : "";
  const total = dims.reduce((s, d) => s + d.total, 0);
  const codeCount = dims.reduce((s, d) => s + d.codes.length, 0);
  const hidden = dims.reduce((s, d) => s + Math.max(d.codes.length - BUGS_PER_DIM, 0), 0);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Haftanın bug'ları{repName ? ` · ${repName}` : isAdmin ? " · ekip" : ""}</h2>
        <span className="hint">
          {weekText} · SONAR-41'de 1-2 puan alan davranışlar, açılıştan kapanışa · {int(total)} bug, {int(codeCount)} kod
          {!all && hidden ? ` · her boyutta en sık ${BUGS_PER_DIM}` : ""}
        </span>
        {isAdmin ? (
          <select
            className="f-input sm"
            data-share-skip
            value={rep}
            onChange={(e) => setRep(e.target.value)}
            aria-label="Bug kartı temsilcisi"
          >
            <option value="">Tüm ekip</option>
            {reps.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      <div className="card-body">
        {dims.length ? (
          dims.map((d) => (
            <div key={d.dim} className="cv-bug-dim" data-share={`gorusme-bug-${d.dim}`}>
              <div className="cv-bug-dim-head">
                <b>
                  {d.dim} · {labels[d.dim]}
                </b>
                <span className="muted">
                  {int(d.total)} bug · {int(d.codes.length)} kod · {repName || "ekip"} · {weekText}
                </span>
                <div className="spacer" />
                {isAdmin ? (
                  <ShareButton
                    kart={`gorusme-bug-${d.dim}`}
                    baslik={`Haftanın bug'ları · ${d.dim} ${labels[d.dim]} · ${repName || "Ekip"} · ${weekText}`}
                  />
                ) : null}
              </div>
              <ol className="cv-bugs">
                {(all ? d.codes : d.codes.slice(0, BUGS_PER_DIM)).map((g) => {
                  const b = sonar.behaviors[g.kod];
                  return (
                    <li key={g.kod}>
                      <div className="cv-bug-head">
                        <span className="cv-code">{g.kod}</span>
                        <b>{b?.name ?? g.kod}</b>
                        <span className="badge critical">
                          {g.list.length} kez{!rep && g.reps > 1 ? ` · ${g.reps} temsilci` : ""}
                        </span>
                      </div>
                      {b?.def ? (
                        <p className="cv-bug-std">
                          <span className="label">Standart</span> {b.def}
                        </p>
                      ) : null}
                      {g.examples.slice(0, all ? 2 : 1).map((x, i) => (
                        <div key={`${x.item_id}-${i}`} className="cv-bug-ex">
                          {x.ihlal || x.alinti ? (
                            <p>
                              <span className="label bad">BUG</span> {x.ihlal || `“${x.alinti}”`}
                            </p>
                          ) : null}
                          {x.dogrusu ? (
                            <p>
                              <span className="label good">DEBUG</span> “{x.dogrusu}”
                            </p>
                          ) : null}
                          <button className="link-btn" onClick={() => onOpen(x.item_id)}>
                            görüşmeyi aç
                          </button>
                        </div>
                      ))}
                    </li>
                  );
                })}
              </ol>
            </div>
          ))
        ) : (
          <div className="empty">Bu hafta {repName ? `${repName} için ` : ""}bug yok.</div>
        )}
        {hidden || dims.some((d) => d.codes.some((g) => g.list.length > 1)) ? (
          <button className="link-btn" onClick={() => setAll((v) => !v)}>
            {all ? "Özet görünüme dön" : `Tüm ${codeCount} kodu ve ikinci örnekleri göster`}
          </button>
        ) : null}
      </div>
    </section>
  );
}

// SONAR-41 genel puanlama tablosu (Yusuf Aras, 25 Ekim 2026); analiz talimatı aynı tabloyu kullanır.
const SCALE: { puan: string; ad: string; tone?: Tone; olcut: string; alinti: string; ornek: string; mesaj: string }[] = [
  {
    puan: "Boş",
    ad: "Fırsat yok",
    olcut: "Davranışı gerektiren an görüşmede doğmadı. Puanlanmaz, ortalamaya girmez.",
    alinti: "Gerekmez",
    ornek: "Müşteri kararı ertelemedi, satış kapandı.",
    mesaj: "Bu görüşmede ölçülmedi.",
  },
  {
    puan: "1",
    ad: "Kaçırıldı",
    tone: "critical",
    olcut: "Fırsat vardı, davranış hiç yapılmadı.",
    alinti: "Fırsatın doğduğu an (müşterinin cümlesi) ve ardından davranışın yokluğu",
    ornek: "Müşteri eşi ve kardeşiyle konuşacağını söyleyince temsilci: “Tabii tabii nasıl isterseniz Murat Bey.” (B28)",
    mesaj: "Fırsatı görmedin.",
  },
  {
    puan: "2",
    ad: "Denendi",
    tone: "critical",
    olcut: "Davranış yapıldı ama yanlış, yüzeysel, yönlendirici, savunmacı ya da baskıcı. Müşteri açılmadı, kapandı ya da rahatsız oldu.",
    alinti: "Temsilcinin hatalı / yarım cümlesi ve müşterinin kapanan cevabı",
    ornek: "“Yani sizi kararsız bırakan nokta nedir? Tedarik anlamında mı sorunlar yaşıyorsunuz?” (B28, cevabı temsilci veriyor) · “Bir iki hafta sonrası için bence indirimli olarak bugün almanız daha mantıklı olur.” (B22, baskı)",
    mesaj: "Fırsatı gördün, yöntem yanlış.",
  },
  {
    puan: "3",
    ad: "Doğru",
    tone: "neutral",
    olcut: "Davranış doğru yapıldı ama kalıp; herkese söylenebilir, müşteriye özel değil.",
    alinti: "Genel, standart temsilci cümlesi",
    ornek: "“Ya tam olarak mesela neyi düşüneceksiniz?” (B28; doğru soru, ama bu müşterinin söylediklerine bağlanmıyor)",
    mesaj: "Doğru yaptın, şimdi bu müşteriye yap.",
  },
  {
    puan: "4",
    ad: "Uyarlanmış",
    tone: "good",
    olcut: "Görüşmede geçen bir bilgiye dayanarak müşteriye özel yapıldı ve müşteri karşılık verdi.",
    alinti: "Temsilcinin cümlesi + müşterinin olumlu / açılan tepkisi",
    ornek: "Ödeme itirazında: “Ben hani hangi kartınız varsa bana söyleyin ben onu size atayım.” → “Birincisi garanti Amerikan Express kredi kartından yapabilirim bu ödemeyi.” (B23)",
    mesaj: "Hedef seviye bu.",
  },
  {
    puan: "5",
    ad: "Örnek",
    tone: "good",
    olcut: "4'ün üstüne zor bir anda yapıldı ya da görüşmeyi somut, tarihli bir adıma çevirdi. Eğitimde dinletilebilir.",
    alinti: "Öncesi ve sonrası: müşterinin tavrı değişti, somut adım kabul edildi",
    ornek: "19 Ekim haftasında 5 alan görüşme yok. İlk 5 buraya girecek.",
    mesaj: "Bu anı ekiple paylaşacağız.",
  },
];

function SonarGuide({ labels, isAdmin }: { labels: Record<string, string>; isAdmin: boolean }) {
  return (
    <section className="card okr-guide" data-share="sonar41-rehber">
      <div className="card-head">
        <h2>SONAR-41 nasıl çalışır?</h2>
        {isAdmin ? <ShareButton kart="sonar41-rehber" baslik="SONAR-41 nasıl çalışır?" /> : null}
      </div>
      <div className="card-body">
        <h3 className="cv-scale-title">Puanlama tablosu</h3>
        <p className="muted cv-scale-note">
          41 davranışın hepsine aynı uygulanır. Her basamak bir öncekine tek bir gözlenebilir koşul ekler:{" "}
          <b>Yaptı mı? → Doğru mu? → Müşteriye özel mi? → Karşılık buldu mu? → Örnek gösterilir mi?</b> Örnekler
          19 Ekim haftasındaki gerçek Rexven görüşmelerinden; çoğu "düşüneyim" anı (B28). "Düşüneyim"de henüz 4 alan
          görüşme olmadığı için 4 ödeme itirazından.
        </p>
        <div className="table-wrap">
          <table className="board cv-scale">
            <thead>
              <tr>
                <th>Puan</th>
                <th>Ölçüt</th>
                <th>Alıntı neyi kanıtlamalı</th>
                <th>Rexven'den gerçek örnek</th>
                <th>Satışçıya mesaj</th>
              </tr>
            </thead>
            <tbody>
              {SCALE.map((r) => (
                <tr key={r.puan}>
                  <td>
                    {r.tone ? <span className={`badge ${r.tone}`}>{r.puan}</span> : <span className="muted">{r.puan}</span>}
                    <div className="cv-scale-name">{r.ad}</div>
                  </td>
                  <td>{r.olcut}</td>
                  <td className="muted">{r.alinti}</td>
                  <td className="cv-scale-example">{r.ornek}</td>
                  <td>
                    <b>{r.mesaj}</b>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted cv-scale-note">
          <b>Kurallar:</b> Alıntı o basamağın koşulunu kanıtlamıyorsa puan bir alta iner; alıntı yoksa puan yazılmaz. Aynı
          fırsatta birden fazla deneme varsa görüşmenin vardığı son durum, aynı davranış için birden fazla fırsat varsa en
          düşük puan esas alınır. 1 ve 2 BUG üretir; DEBUG yalnızca görüşmede geçen bilgiyle yazılır. Müşteriye
          söylenmeyen B39'da (CRM notu) 4-5 için müşteri tepkisi aranmaz, notun özelliği ve netliği ölçülür. Puan prim ya
          da ücrete bağlanmaz, koçluk içindir.
        </p>
        <div className="okr-guide-grid">
          <div>
            <h3>Ne ölçer</h3>
            <p>
              Satış görüşmesinde olması gereken 41 davranış (B01-B41), görüşmenin akışına göre 5 boyutta:{" "}
              {Object.entries(labels)
                .map(([k, v]) => `${k} ${v}`)
                .join(" → ")}
              .
            </p>
          </div>
          <div>
            <h3>Kayıttan puana</h3>
            <p>
              Telefon kaydı (Verimor) ve toplantı kaydı (Zoho Meeting) her gece yazıya dökülür, temsilci ve müşteri ayrılır. 1
              dakikadan kısa çağrılar alınmaz. Yapay zekâ her görüşmeyi 41 davranışın tanımıyla okur.
            </p>
          </div>
          <div>
            <h3>Kanıt</h3>
            <p>Her puanın yanında görüşmeden birebir alıntı vardır. Alıntısı olmayan puan, güçlü yan ya da gelişim maddesi yazılmaz.</p>
          </div>
          <div>
            <h3>Boyut puanı</h3>
            <p>
              Görüşmede o boyutta puanlanan davranışların ortalaması; haftalık puan bu görüşmelerin ortalaması. Altındaki
              "zayıf / güçlü" sayıları 1-2 ve 4-5 alan davranışlardır: ikisi birlikte yüksekse temsilci yapabiliyor ama tutarsız.
            </p>
          </div>
          <div>
            <h3>Renkler</h3>
            <p>
              Ortalamalar puanlama tablosunun basamaklarıyla renklenir: <span className="badge good">4+</span> uyarlanmış
              (hedef) · <span className="badge neutral">3-3,9</span> doğru ama kalıp ·{" "}
              <span className="badge warning">2-2,9</span> deneniyor, yöntem yanlış ·{" "}
              <span className="badge critical">2 altı</span> çoğunlukla kaçırılıyor.
            </p>
          </div>
          <div>
            <h3>BUG ve DEBUG</h3>
            <p>
              1-2 alan davranış bir BUG'dır; alıntı, ihlalin olduğu andır. DEBUG, temsilcinin o anda söylemesi gereken cümledir ve
              yalnız görüşmede geçen bilgiyle yazılır.
            </p>
          </div>
          <div>
            <h3>CRM notu</h3>
            <p>
              Görüşmeden sonraki 3 saat içinde lead'e yazılan not o görüşmenindir (B39). Not yoksa B39 = 1. Not varsa C1-C6 ile
              puanlanır: durum doğruluğu, ihtiyaç ve profil, gerçek engel, karar süreci, sonraki adım ve tarih, nesnellik.
            </p>
          </div>
          <div>
            <h3>Örnek görüşmeler</h3>
            <p>En az 8 davranışı puanlanmış, en az 3'ü 4-5 alan görüşmeler ortalamaya göre sıralanır; iyi anlar akış sırasıyla gösterilir.</p>
          </div>
          <div>
            <h3>Ritim</h3>
            <p>
              Analiz her gece güncellenir, hafta pazartesi kesinleşir. Temsilciye ve kanala gönderim otomatik değildir; yönetici
              butonla gönderir.
            </p>
          </div>
          <div>
            <h3>Sınır</h3>
            <p>
              Puanları yapay zekâ verir. Tek görüşmenin puanı oynak olabilir; haftalık ortalamaya bakın, az görüşmeye dayanan
              boyutlara temkinli yaklaşın. Puan prim ya da ücretle bağlanmaz, koçluk içindir.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

const REQUEST_DAYS = [30, 90, 180, 365];

/** Özel görüşme analizi: numaranın kayıtlı görüşmeleri analiz kuyruğuna önce alınır. */
function RequestCard({ onOpen }: { onOpen: (id: number) => void }) {
  const [phone, setPhone] = useState("");
  const [days, setDays] = useState(90);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const { data, refresh } = usePolling<{ requests: ConvRequest[] }>("/api/conversations/requests", 5 * 60_000);

  async function submit() {
    if (!phone.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api<{ id: number; phone: string; kayitli: number; kisa: number; gorusme: number }>(
        "/api/conversations/requests",
        { method: "POST", body: JSON.stringify({ phone: phone.trim(), days }) },
      );
      setMsg({
        ok: r.gorusme > 0,
        text: r.gorusme
          ? `${r.phone}: ${r.gorusme} görüşme analize alındı${r.kisa ? ` (${r.kisa} çağrı 1 dk altı, alınmadı)` : ""}.`
          : `${r.phone}: ${r.kayitli} kayıtlı çağrının hepsi 1 dk altı; analiz edilecek görüşme yok.`,
      });
      setPhone("");
      setOpenId(r.id);
      refresh();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : "Talep oluşturulamadı" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Özel görüşme analizi</h2>
        <span className="hint">numaranın kayıtlı görüşmeleri, haftadan bağımsız, sıranın başına alınır</span>
      </div>
      <div className="card-body">
        <div className="cv-request-form">
          <input
            className="f-input"
            value={phone}
            inputMode="tel"
            placeholder="Telefon: 0532 123 45 67"
            onChange={(e) => setPhone(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            aria-label="Telefon numarası"
          />
          <select className="f-input sm" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Dönem">
            {REQUEST_DAYS.map((d) => (
              <option key={d} value={d}>
                son {d} gün
              </option>
            ))}
          </select>
          <button className="btn primary" onClick={submit} disabled={busy || !phone.trim()}>
            {busy ? "Aranıyor…" : "Analiz talep et"}
          </button>
        </div>
        {msg ? <div className={`banner${msg.ok ? "" : " error"}`}>{msg.text}</div> : null}
        <p className="muted cv-request-note">
          Kayıtlar gece işinde (01:30) yazıya dökülüp analiz edilir; talep edilenler önce işlenir. Önceden analiz edilmiş
          görüşmeler hemen görünür.
        </p>
        {data?.requests.length ? (
          <ul className="cv-requests">
            {data.requests.map((r) => {
              const done = r.durum.analiz_edildi ?? 0;
              const open = openId === r.id;
              return (
                <li key={r.id}>
                  <button className="cv-request-head" onClick={() => setOpenId(open ? null : r.id)} aria-expanded={open}>
                    <span className="caret" aria-hidden>
                      {open ? "▾" : "▸"}
                    </span>
                    <b>{r.customer ?? r.phone}</b>
                    <span className="muted">
                      {r.customer ? `${r.phone} · ` : ""}son {r.days} gün · {ago(r.created_at, new Date())}
                    </span>
                    <span className="spacer" />
                    <span className={`badge ${done === r.items.length && r.items.length ? "good" : "neutral"}`}>
                      {done}/{r.items.length} analiz edildi
                    </span>
                  </button>
                  {open ? (
                    r.items.length ? (
                      <ItemTable items={r.items} showRep onOpen={onOpen} />
                    ) : (
                      <div className="empty">Analiz edilecek görüşme yok.</div>
                    )
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </section>
  );
}

/** Seçilen haftada analiz yoksa nedenini söyler ve analizi olan son haftaya götürür. */
function EmptyWeek({
  week,
  current,
  waiting,
  latest,
  onPick,
}: {
  week: string;
  current: string;
  waiting: number;
  latest: string | null;
  onPick: (week: string) => void;
}) {
  const reason =
    week === current
      ? "Bu haftanın görüşmeleri her gece toplanır, kotaya göre seçilip analiz edilir."
      : waiting
        ? `Bu haftadan ${waiting} görüşme kotayla seçildi; yapay zekâ analizi bekliyor (ANTHROPIC_API_KEY eklenince gece işlenir).`
        : "Bu hafta için analiz edilmiş görüşme yok.";
  return (
    <div className="banner">
      <strong>Bu hafta henüz analiz yok.</strong> {reason}
      {latest && latest !== week ? (
        <>
          {" "}
          <button className="link-btn" onClick={() => onPick(latest)}>
            Analizi olan son haftayı aç ({weekLabel(latest, current)})
          </button>
        </>
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

function TeamCard({ n, weekText }: { n: NonNullable<NonNullable<ConvWeek["team"]>["narrative"]>; weekText: string }) {
  return (
    <section className="card" data-share="gorusme-haftanin-ozeti">
      <div className="card-head">
        <h2>Haftanın özeti · {weekText}</h2>
        <span className="hint">yapay zekâ yorumu · sayılar koddan</span>
        <ShareButton kart="gorusme-haftanin-ozeti" baslik={`Görüşme analizleri · haftanın özeti · ${weekText}`} />
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
              {a.musaitlik?.temsilci_sordu ? (
                <p className="warn-text">Temsilci “müsait misiniz” diye sordu (B01).</p>
              ) : null}
              {a.musaitlik?.musait_degil ? (
                <div className="cv-hold">
                  <strong>Müsait değil anı:</strong> {HOLD_LABEL[a.musaitlik.tutma] ?? a.musaitlik.tutma}
                  {a.musaitlik.geri_arama_net ? " · geri arama zamanı net" : " · geri arama zamanı belirsiz"}
                  {a.musaitlik.alinti ? <blockquote className="cv-quote">“{a.musaitlik.alinti}”</blockquote> : null}
                </div>
              ) : null}
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
  rows: { kod: string; puan: number; alinti: string; dogrusu?: string }[];
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
                    {r.dogrusu ? (
                      <div className="cv-bug-fix">
                        <span className="label good">DEBUG</span> “{r.dogrusu}”
                      </div>
                    ) : null}
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
