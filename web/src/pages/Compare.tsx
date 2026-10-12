import { useEffect, useMemo, useState } from "react";

import { api, usePolling } from "../api";
import { CompareChart } from "../components/CompareChart";
import { duration, int, minutes, num, pct } from "../format";
import type { ComparePayload, CompareSide, Kpis, Me, RepRef } from "../types";

type Mode = "donem" | "kisi";
type Range = [string, string];

// --- tarih yardımcıları (Istanbul günü, ISO "YYYY-AA-GG") ---------------------

function istanbulToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
}

function shift(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function monday(iso: string): string {
  const dow = new Date(`${iso}T12:00:00Z`).getUTCDay();
  return shift(iso, -((dow + 6) % 7));
}

const monthStart = (iso: string) => `${iso.slice(0, 7)}-01`;

const shortDay = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const weekday = new Intl.DateTimeFormat("tr-TR", { weekday: "long", timeZone: "UTC" });

function rangeLabel([first, last]: Range): string {
  const f = new Date(`${first}T12:00:00Z`);
  if (first === last) return `${shortDay.format(f)} ${weekday.format(f)}`;
  return `${shortDay.format(f)} – ${shortDay.format(new Date(`${last}T12:00:00Z`))}`;
}

// --- hazır seçimler ------------------------------------------------------------

interface PeriodPair {
  key: string;
  label: string;
  make: (t: string) => [Range, Range];
}

const PERIOD_PAIRS: PeriodPair[] = [
  { key: "gun", label: "Bugün ↔ geçen hafta aynı gün", make: (t) => [[t, t], [shift(t, -7), shift(t, -7)]] },
  { key: "dun", label: "Dün ↔ önceki gün", make: (t) => [[shift(t, -1), shift(t, -1)], [shift(t, -2), shift(t, -2)]] },
  {
    key: "hafta",
    label: "Bu hafta ↔ geçen hafta",
    make: (t) => [[monday(t), t], [shift(monday(t), -7), shift(monday(t), -1)]],
  },
  {
    key: "ay",
    label: "Bu ay ↔ geçen ay",
    make: (t) => {
      const prevLast = shift(monthStart(t), -1);
      return [[monthStart(t), t], [monthStart(prevLast), prevLast]];
    },
  },
  {
    key: "30",
    label: "Son 30 gün ↔ önceki 30 gün",
    make: (t) => [[shift(t, -29), t], [shift(t, -59), shift(t, -30)]],
  },
];

interface Period {
  key: string;
  label: string;
  make: (t: string) => Range;
}

const PERIODS: Period[] = [
  { key: "bugun", label: "Bugün", make: (t) => [t, t] },
  { key: "dun", label: "Dün", make: (t) => [shift(t, -1), shift(t, -1)] },
  { key: "hafta", label: "Bu hafta", make: (t) => [monday(t), t] },
  { key: "gecen", label: "Geçen hafta", make: (t) => [shift(monday(t), -7), shift(monday(t), -1)] },
  { key: "ay", label: "Bu ay", make: (t) => [monthStart(t), t] },
  { key: "30", label: "Son 30 gün", make: (t) => [shift(t, -29), t] },
];

// --- ölçüler -------------------------------------------------------------------

type Kind = "count" | "rate" | "duration" | "minutes";

interface Metric {
  key: keyof Kpis;
  label: string;
  kind: Kind;
  hint?: string;
  lowerBetter?: boolean;
  sample?: keyof Kpis;
}

const SECTIONS: { title: string; metrics: Metric[] }[] = [
  {
    title: "Hacim",
    metrics: [
      { key: "arama", label: "Arama", kind: "count", hint: "Giden çevirme" },
      { key: "ulasilan", label: "Ulaşılan", kind: "count", hint: "Giden temas + dönüş" },
      { key: "donus", label: "Dönüş", kind: "count" },
      { key: "whatsapp", label: "WhatsApp mesajı", kind: "count" },
    ],
  },
  {
    title: "Verim",
    metrics: [
      { key: "ulasma_orani", label: "Ulaşma oranı", kind: "rate", sample: "lead_payda", hint: "Benzersiz lead üzerinden" },
      { key: "sure_ort", label: "Ort. görüşme süresi", kind: "duration" },
      { key: "call_sec", label: "Toplam konuşma", kind: "minutes" },
      { key: "doluluk", label: "Doluluk", kind: "rate", sample: "mesai_dk", hint: "Dolu / (dolu + kayıp); mola hariç" },
      { key: "kayip_dk", label: "Kayıp zaman", kind: "minutes", lowerBetter: true, hint: "Mola dışı boşluk; az olan önde" },
    ],
  },
  {
    title: "Sonuç",
    metrics: [
      { key: "randevu", label: "Toplantı", kind: "count", hint: "Saati gelen, iptal hariç" },
      { key: "katildi", label: "Katıldı (CRM sonucu)", kind: "count" },
      { key: "katilim_orani", label: "Katılım oranı", kind: "rate", sample: "katilim_payda" },
      {
        key: "sonuc_girilmedi",
        label: "Sonucu girilmemiş",
        kind: "count",
        lowerBetter: true,
        hint: "Az olan önde",
      },
    ],
  },
];

const MIN_SAMPLE = 5;

/** Günlük ortalamada sayılar mesai gününe bölünür; oranlar ve ortalamalar değişmez. */
function value(side: CompareSide, m: Metric, perDay: boolean): number | null {
  const raw = side.kpis[m.key] as number | null;
  if (raw === null || raw === undefined) return null;
  const v = m.key === "call_sec" ? raw / 60 : raw;
  if (!perDay || m.kind === "rate" || m.kind === "duration") return v;
  return side.work_days > 0 ? v / side.work_days : null;
}

function show(v: number | null, m: Metric, perDay: boolean): string {
  if (v === null) return "—";
  if (m.kind === "rate") return pct(v);
  if (m.kind === "duration") return duration(v);
  if (m.kind === "minutes") return minutes(v);
  return perDay || !Number.isInteger(v) ? num(v) : int(v);
}

function thin(side: CompareSide, m: Metric): boolean {
  if (!m.sample) return false;
  const n = Number(side.kpis[m.sample] ?? 0);
  return m.key === "doluluk" ? n <= 0 : n < MIN_SAMPLE;
}

type Winner = "a" | "b" | "tie" | null;

function winner(a: number | null, b: number | null, m: Metric, skip: boolean, perDay: boolean): Winner {
  if (skip || a === null || b === null) return null;
  if (show(a, m, perDay) === show(b, m, perDay)) return "tie";
  return (a > b) !== Boolean(m.lowerBetter) ? "a" : "b";
}

// --- sayfa ---------------------------------------------------------------------

interface Subject {
  id: string;
  label: string;
}

export function ComparePage({ me }: { me: Me }) {
  const today = istanbulToday();
  const isAdmin = me.role === "admin";
  const [reps, setReps] = useState<RepRef[]>([]);
  const [mode, setMode] = useState<Mode>(isAdmin ? "donem" : "kisi");
  const [pairKey, setPairKey] = useState("hafta");
  const [periodKey, setPeriodKey] = useState("hafta");
  const [subject, setSubject] = useState(isAdmin ? "team" : (me.rep_id ?? "avg"));
  const [subjectA, setSubjectA] = useState(isAdmin ? "" : (me.rep_id ?? "avg"));
  const [subjectB, setSubjectB] = useState("avg");
  const [customA, setCustomA] = useState<Range>([monday(today), today]);
  const [customB, setCustomB] = useState<Range>([shift(monday(today), -7), shift(monday(today), -1)]);
  const [align, setAlign] = useState(true);
  const [perDay, setPerDay] = useState<boolean | null>(null);

  useEffect(() => {
    if (!isAdmin) return;
    api<RepRef[]>("/api/reps")
      .then((rows) => {
        setReps(rows);
        setSubjectA((cur) => cur || rows[0]?.rep_id || "team");
        setSubjectB((cur) => (cur === "avg" && rows[1] ? rows[1].rep_id : cur));
      })
      .catch(() => setReps([]));
  }, [isAdmin]);

  const subjects: Subject[] = useMemo(() => {
    const base = [
      { id: "team", label: "Ekip toplamı" },
      { id: "avg", label: "Temsilci ortalaması" },
    ];
    if (!isAdmin) return me.rep_id ? [{ id: me.rep_id, label: "Ben" }, ...base] : base;
    return [...base, ...reps.map((r) => ({ id: r.rep_id, label: r.full_name }))];
  }, [isAdmin, me.rep_id, reps]);

  let rangeA: Range;
  let rangeB: Range;
  let subA: string;
  let subB: string;
  if (mode === "donem") {
    const pair = PERIOD_PAIRS.find((p) => p.key === pairKey);
    [rangeA, rangeB] = pair ? pair.make(today) : [customA, customB];
    subA = subB = subject;
  } else {
    const period = PERIODS.find((p) => p.key === periodKey);
    rangeA = rangeB = period ? period.make(today) : customA;
    subA = subjectA;
    subB = subjectB;
  }

  const ready = Boolean(subA && subB);
  const touchesToday = rangeA[1] >= today || rangeB[1] >= today;
  const query = new URLSearchParams({
    a_from: rangeA[0],
    a_to: rangeA[1],
    a_subject: subA,
    b_from: rangeB[0],
    b_to: rangeB[1],
    b_subject: subB,
    align: String(mode === "kisi" || align),
  });
  const { data, error, loading, refresh } = usePolling<ComparePayload>(
    ready ? `/api/compare?${query}` : null,
    touchesToday ? 60_000 : 0,
  );

  const autoPerDay = data ? Math.abs(data.a.work_days - data.b.work_days) > 0.05 : false;
  const daily = perDay ?? autoPerDay;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Karşılaştır</h1>
          <div className="sub">İki dönemi ya da iki kişiyi yan yana koy, hangi kriterde kim önde gör.</div>
        </div>
        <div className="controls">
          {loading ? <span className="freshness">yükleniyor…</span> : null}
          <button className="btn" onClick={refresh}>
            Yenile
          </button>
        </div>
      </div>

      <section className="card card-body cmp-setup">
        <div className="seg" role="tablist" aria-label="Karşılaştırma türü">
          <button role="tab" aria-selected={mode === "donem"} onClick={() => setMode("donem")}>
            İki dönem
          </button>
          <button role="tab" aria-selected={mode === "kisi"} onClick={() => setMode("kisi")}>
            İki kişi
          </button>
        </div>

        {mode === "donem" ? (
          <>
            <label className="field">
              <span>Kim</span>
              <SubjectSelect value={subject} options={subjects} onChange={setSubject} />
            </label>
            <div className="chips" role="radiogroup" aria-label="Dönemler">
              {PERIOD_PAIRS.map((p) => (
                <Chip key={p.key} on={pairKey === p.key} onClick={() => setPairKey(p.key)}>
                  {p.label}
                </Chip>
              ))}
              <Chip on={pairKey === "ozel"} onClick={() => setPairKey("ozel")}>
                Özel tarih
              </Chip>
            </div>
            {pairKey === "ozel" ? (
              <div className="cmp-custom">
                <RangeInput label="A dönemi" value={customA} max={today} onChange={setCustomA} />
                <RangeInput label="B dönemi" value={customB} max={today} onChange={setCustomB} />
              </div>
            ) : null}
            <label className="check" title="Süren dönem, geçmiş dönemin aynı anına kadar olan kısmıyla kıyaslanır">
              <input type="checkbox" checked={align} onChange={(e) => setAlign(e.target.checked)} />
              Aynı ana kadar kıyasla
            </label>
          </>
        ) : (
          <>
            <div className="cmp-custom">
              <label className="field">
                <span>
                  <i className="swatch a" /> A
                </span>
                <SubjectSelect value={subjectA} options={subjects} onChange={setSubjectA} />
              </label>
              <label className="field">
                <span>
                  <i className="swatch b" /> B
                </span>
                <SubjectSelect value={subjectB} options={subjects} onChange={setSubjectB} />
              </label>
            </div>
            <div className="chips" role="radiogroup" aria-label="Dönem">
              {PERIODS.map((p) => (
                <Chip key={p.key} on={periodKey === p.key} onClick={() => setPeriodKey(p.key)}>
                  {p.label}
                </Chip>
              ))}
              <Chip on={periodKey === "ozel"} onClick={() => setPeriodKey("ozel")}>
                Özel tarih
              </Chip>
            </div>
            {periodKey === "ozel" ? (
              <div className="cmp-custom">
                <RangeInput label="Dönem" value={customA} max={today} onChange={setCustomA} />
              </div>
            ) : null}
          </>
        )}
      </section>

      {error ? <div className="banner error">Veri alınamadı: {error.message}</div> : null}

      {!data ? (
        <div className="skeleton" style={{ height: 320 }} />
      ) : (
        <CompareBody data={data} rangeA={rangeA} rangeB={rangeB} daily={daily} setDaily={setPerDay} mode={mode} />
      )}
    </>
  );
}

function CompareBody({
  data,
  rangeA,
  rangeB,
  daily,
  setDaily,
  mode,
}: {
  data: ComparePayload;
  rangeA: Range;
  rangeB: Range;
  daily: boolean;
  setDaily: (v: boolean) => void;
  mode: Mode;
}) {
  const { a, b } = data;
  const rows = SECTIONS.map((s) => ({
    ...s,
    rows: s.metrics.map((m) => {
      const va = value(a, m, daily);
      const vb = value(b, m, daily);
      const skip = thin(a, m) || thin(b, m);
      return { m, va, vb, skip, win: winner(va, vb, m, skip, daily) };
    }),
  }));
  const all = rows.flatMap((s) => s.rows);
  const winsA = all.filter((r) => r.win === "a").length;
  const winsB = all.filter((r) => r.win === "b").length;
  const titleA = mode === "donem" ? rangeLabel(rangeA) : a.label;
  const titleB = mode === "donem" ? rangeLabel(rangeB) : b.label;

  return (
    <>
      <div className="vs">
        <Contender side={a} tag="a" title={titleA} sub={mode === "donem" ? a.label : rangeLabel(rangeA)} wins={winsA} total={all.length} lead={winsA > winsB} />
        <div className="vs-mid" aria-hidden>
          VS
        </div>
        <Contender side={b} tag="b" title={titleB} sub={mode === "donem" ? b.label : rangeLabel(rangeB)} wins={winsB} total={all.length} lead={winsB > winsA} />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Kriter kriter</h2>
          <div className="seg small" role="tablist" aria-label="Gösterim">
            <button role="tab" aria-selected={!daily} onClick={() => setDaily(false)}>
              Toplam
            </button>
            <button role="tab" aria-selected={daily} onClick={() => setDaily(true)}>
              Günlük ortalama
            </button>
          </div>
        </div>
        <div className="card-body">
          {daily ? (
            <p className="muted cmp-note">
              Sayılar mesai gününe bölündü (A {num(a.work_days)} gün, B {num(b.work_days)} gün). Oranlar ve süre
              ortalamaları aynen kalır.
            </p>
          ) : a.work_days !== b.work_days ? (
            <p className="muted cmp-note">
              Dönemlerin mesai günü farklı (A {num(a.work_days)}, B {num(b.work_days)}); adil kıyas için “Günlük
              ortalama”ya geçin.
            </p>
          ) : null}
          <table className="spec">
            <thead>
              <tr>
                <th className="sa">
                  <i className="swatch a" /> {titleA}
                </th>
                <th className="mid">Kriter</th>
                <th className="sb">
                  {titleB} <i className="swatch b" />
                </th>
              </tr>
            </thead>
            {rows.map((s) => (
              <tbody key={s.title}>
                <tr className="section">
                  <th colSpan={3}>{s.title}</th>
                </tr>
                {s.rows.map(({ m, va, vb, skip, win }) => (
                  <SpecRow key={m.key} m={m} va={va} vb={vb} skip={skip} win={win} daily={daily} />
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>{a.series_group === "hour" && b.series_group === "hour" ? "Saat saat" : "Gün gün"}</h2>
          <span className="hint">A ve B aynı eksende · içi boş nokta süren dilim</span>
        </div>
        <div className="card-body">
          <CompareChart a={a} b={b} titleA={titleA} titleB={titleB} />
        </div>
      </section>
    </>
  );
}

function Contender({
  side,
  tag,
  title,
  sub,
  wins,
  total,
  lead,
}: {
  side: CompareSide;
  tag: "a" | "b";
  title: string;
  sub: string;
  wins: number;
  total: number;
  lead: boolean;
}) {
  const cut = new Date(side.cut);
  const cutText = new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Istanbul",
  }).format(cut);
  return (
    <div className={`card contender ${tag}${lead ? " lead" : ""}`}>
      <div className="c-tag">
        <i className={`swatch ${tag}`} /> {tag.toUpperCase()}
        {lead ? <span className="badge neutral">◆ önde</span> : null}
      </div>
      <div className="c-title">{title}</div>
      <div className="c-sub">{sub}</div>
      <div className="c-score">
        <b>{wins}</b>
        <span>/ {total} kriterde önde</span>
      </div>
      <div className="c-meta">
        {num(side.work_days)} mesai günü
        {side.aligned ? ` · ${cutText}'e kadar` : ""}
        {side.n_reps > 1 ? ` · ${side.n_reps} kişi` : ""}
      </div>
    </div>
  );
}

function SpecRow({
  m,
  va,
  vb,
  skip,
  win,
  daily,
}: {
  m: Metric;
  va: number | null;
  vb: number | null;
  skip: boolean;
  win: Winner;
  daily: boolean;
}) {
  const max = Math.max(va ?? 0, vb ?? 0) || 1;
  const wa = ((va ?? 0) / max) * 100;
  const wb = ((vb ?? 0) / max) * 100;
  const diff = va !== null && vb !== null && vb !== 0 && m.kind !== "rate" ? ((va - vb) / Math.abs(vb)) * 100 : null;
  const pts = va !== null && vb !== null && m.kind === "rate" ? va - vb : null;
  const delta =
    pts !== null
      ? `${pts >= 0 ? "+" : ""}${num(pts)} puan`
      : diff !== null
        ? `${diff >= 0 ? "+" : ""}%${num(Math.round(diff))}`
        : null;
  return (
    <tr className={win ? `win-${win}` : undefined}>
      <td className="sa">
        <div className="v">
          {win === "a" ? <span className="crown" aria-label="önde">◆</span> : null}
          {show(va, m, daily)}
        </div>
        <div className="bar-track a">
          <span style={{ width: `${wa}%` }} />
        </div>
      </td>
      <td className="mid" title={m.hint}>
        <div className="k">{m.label}</div>
        <div className="d">
          {skip ? "örneklem az" : win === "tie" ? "başa baş" : delta ? `A, B'ye göre ${delta}` : ""}
        </div>
      </td>
      <td className="sb">
        <div className="v">
          {show(vb, m, daily)}
          {win === "b" ? <span className="crown" aria-label="önde">◆</span> : null}
        </div>
        <div className="bar-track b">
          <span style={{ width: `${wb}%` }} />
        </div>
      </td>
    </tr>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button className="chip" role="radio" aria-checked={on} onClick={onClick}>
      {children}
    </button>
  );
}

function SubjectSelect({
  value: v,
  options,
  onChange,
}: {
  value: string;
  options: Subject[];
  onChange: (id: string) => void;
}) {
  return (
    <select value={v} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

function RangeInput({
  label,
  value: [first, last],
  max,
  onChange,
}: {
  label: string;
  value: Range;
  max: string;
  onChange: (r: Range) => void;
}) {
  return (
    <fieldset className="range">
      <legend>{label}</legend>
      <input
        type="date"
        value={first}
        max={max}
        aria-label={`${label} başlangıç`}
        onChange={(e) => e.target.value && onChange([e.target.value, e.target.value > last ? e.target.value : last])}
      />
      <span className="muted">–</span>
      <input
        type="date"
        value={last}
        min={first}
        max={max}
        aria-label={`${label} bitiş`}
        onChange={(e) => e.target.value && onChange([first, e.target.value])}
      />
    </fieldset>
  );
}
