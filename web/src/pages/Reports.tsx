import { useMemo, useState } from "react";

import { usePolling } from "../api";
import { Funnel } from "../components/Funnel";
import { StatTile } from "../components/StatTile";
import { duration, int, num, pct, tl, tlShort } from "../format";
import type { CiroMonth, Me, ReportRow, SalesReport } from "../types";

type Preset = "bu_ay" | "gecen_ay" | "son_30" | "son_90";

const PRESETS: { key: Preset; label: string }[] = [
  { key: "bu_ay", label: "Bu ay" },
  { key: "gecen_ay", label: "Geçen ay" },
  { key: "son_30", label: "Son 30 gün" },
  { key: "son_90", label: "Son 90 gün" },
];

const AVG = "avg";

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function istanbulToday(): Date {
  const s = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
  return new Date(`${s}T00:00:00Z`);
}

function range(p: Preset): [string, string] {
  const t = istanbulToday();
  const y = t.getUTCFullYear();
  const m = t.getUTCMonth();
  if (p === "bu_ay") return [iso(new Date(Date.UTC(y, m, 1))), iso(t)];
  if (p === "gecen_ay") return [iso(new Date(Date.UTC(y, m - 1, 1))), iso(new Date(Date.UTC(y, m, 0)))];
  const days = p === "son_30" ? 30 : 90;
  return [iso(new Date(t.getTime() - (days - 1) * 86400000)), iso(t)];
}

const dayLabel = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "long", timeZone: "UTC" });

export function ReportsPage({ me }: { me: Me }) {
  const [preset, setPreset] = useState<Preset>("bu_ay");
  const [start, end] = range(preset);
  const { data, error, loading } = usePolling<SalesReport>(`/api/sales-report?start=${start}&end=${end}`, 0);
  const isAdmin = me.role === "admin";
  const [sideA, setSideA] = useState<string | null>(null);
  const [sideB, setSideB] = useState<string>(AVG);

  const pick = (id: string | null): [ReportRow | undefined, string] => {
    if (!data) return [undefined, ""];
    if (id === AVG) return [data.avg, "Ekip ortalaması"];
    const row = data.reps.find((r) => r.rep_id === id);
    return [row, row?.name ?? ""];
  };
  const defaultA = isAdmin ? [...(data?.reps ?? [])].sort((x, y) => y.ciro - x.ciro)[0]?.rep_id : me.rep_id;
  const [a, labelA] = pick(sideA ?? defaultA ?? AVG);
  const [b, labelB] = pick(isAdmin ? sideB : AVG);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Raporlar</h1>
          <div className="sub">
            {data
              ? `${dayLabel.format(new Date(`${data.start}T00:00:00Z`))} – ${dayLabel.format(new Date(`${data.end}T00:00:00Z`))} · ${data.work_days} iş günü`
              : "…"}
            {loading ? " · yükleniyor…" : ""}
          </div>
        </div>
        <div className="controls">
          <div className="chips" role="group" aria-label="Dönem">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                className={`chip${preset === p.key ? " on" : ""}`}
                onClick={() => setPreset(p.key)}
                aria-pressed={preset === p.key}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error ? <div className="banner error">Rapor alınamadı: {error.message}</div> : null}
      {data && data.start > start ? (
        <div className="banner">
          Veri {dayLabel.format(new Date(`${data.data_start}T00:00:00Z`))} 2026'dan itibaren tam; dönem oradan başlatıldı.
        </div>
      ) : null}

      {!data || !a || !b ? (
        <div className="tiles">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      ) : (
        <>
          <div className="tiles">
            <StatTile label="Ekip cirosu" value={tlShort(data.team.ciro)} expected={`${int(data.team.satis)} satış`} />
            <StatTile label="Ortalama satış" value={tlShort(data.team.ort_satis)} expected="satış başına ciro" />
            <StatTile
              label="Kişi başı ciro"
              value={tlShort(data.avg.ciro)}
              expected={`günde ${tlShort(data.avg.gunluk_ciro)}`}
            />
            <StatTile
              label="Ulaşılandan satış"
              value={pct(data.team.satis_orani)}
              expected={`${int(data.team.lead_pay)} ulaşılan lead`}
            />
          </div>

          <div className="grid-2">
            <section className="card">
              <div className="card-head">
                <h2>Satış hunisi</h2>
                {isAdmin ? (
                  <div className="fn-pickers">
                    <SidePicker value={sideA ?? defaultA ?? AVG} onChange={setSideA} reps={data.reps} cls="a" />
                    <span className="muted">ile</span>
                    <SidePicker value={sideB} onChange={setSideB} reps={data.reps} cls="b" />
                  </div>
                ) : (
                  <span className="hint">sen ve ekip ortalaması</span>
                )}
              </div>
              <div className="card-body">
                <Funnel a={a} b={b} labelA={labelA} labelB={labelB} />
                <p className="muted fn-note">
                  Dönemdeki faaliyetten kurulur. Satışların çoğu toplantısız (telefonda) kapandığı için satış, katılan
                  toplantıya değil ulaşılan lead'e oranlanır.
                </p>
              </div>
            </section>
            <section className="card">
              <div className="card-head">
                <h2>Aylık ekip cirosu</h2>
                <span className="hint">satış ekibi · Nisan 2026'dan</span>
              </div>
              <div className="card-body">
                <CiroChart months={data.ciro_monthly} />
                {isAdmin ? <CiroShare reps={data.reps} total={data.team.ciro} /> : null}
              </div>
            </section>
          </div>

          <section className="card">
            <div className="card-head">
              <h2>{isAdmin ? "Ekip sıralaması ve metrikler" : "Metriklerim"}</h2>
              <span className="hint">{isAdmin ? "sütun başlığına tıkla: sırala" : "ekip ortalamasıyla"}</span>
            </div>
            <div className="card-body">
              <RankTable data={data} ranked={isAdmin} />
            </div>
          </section>
        </>
      )}
    </>
  );
}

function SidePicker({
  value,
  onChange,
  reps,
  cls,
}: {
  value: string;
  onChange: (v: string) => void;
  reps: ReportRow[];
  cls: string;
}) {
  return (
    <label className="fn-picker">
      <span className={`swatch ${cls}`} />
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value={AVG}>Ekip ortalaması</option>
        {reps.map((r) => (
          <option key={r.rep_id} value={r.rep_id}>
            {r.name}
          </option>
        ))}
      </select>
    </label>
  );
}

// --- sıralama tablosu ---------------------------------------------------------

interface Col {
  key: keyof ReportRow;
  label: string;
  fmt: (v: number | null) => string;
  title?: string;
  /** Küçük olan iyi (satış döngüsü). */
  low?: boolean;
}

const COLS: Col[] = [
  { key: "ciro", label: "Ciro", fmt: (v) => tl(v) },
  { key: "satis", label: "Satış", fmt: int },
  { key: "ort_satis", label: "Ort. satış", fmt: (v) => tl(v) },
  { key: "lead_payda", label: "Aranan lead", fmt: int },
  { key: "ulasma_orani", label: "Ulaşma", fmt: pct },
  { key: "randevu", label: "Toplantı", fmt: int },
  { key: "toplanti_donus", label: "Toplantıya dönüş", fmt: pct, title: "Toplantı / ulaşılan lead" },
  { key: "katilim_orani", label: "Katılım", fmt: pct },
  { key: "satis_orani", label: "Ulaşılandan satış", fmt: pct },
  { key: "gunluk_arama", label: "Günlük arama", fmt: num },
  { key: "sure_ort", label: "Ort. görüşme", fmt: (v) => duration(v) },
  { key: "whatsapp", label: "WhatsApp", fmt: int },
  {
    key: "doluluk",
    label: "Doluluk",
    fmt: pct,
    title: "Geçmiş günlerde WhatsApp (Temmuz–Eylül) ve CRM işlemleri eksik; gerçek değer daha yüksek.",
  },
  { key: "dongu_gun", label: "Satış döngüsü", fmt: (v) => (v === null ? "—" : `${num(v)} gün`), low: true, title: "Atanmadan satışa, medyan" },
  { key: "atanan", label: "Atanan lead", fmt: int },
];

const read = (row: ReportRow, key: keyof ReportRow): number | null => {
  const v = row[key];
  return typeof v === "number" ? v : null;
};

function RankTable({ data, ranked }: { data: SalesReport; ranked: boolean }) {
  const [sort, setSort] = useState<keyof ReportRow>("ciro");
  const col = COLS.find((c) => c.key === sort) ?? COLS[0];
  const rows = useMemo(
    () =>
      [...data.reps].sort((x, y) => {
        const a = read(x, sort);
        const b = read(y, sort);
        if (a === null) return 1;
        if (b === null) return -1;
        return col.low ? a - b : b - a;
      }),
    [data.reps, sort, col.low],
  );
  const best = new Map(
    COLS.map((c) => {
      const vals = data.reps.map((r) => read(r, c.key)).filter((v): v is number => v !== null);
      return [c.key, vals.length > 1 ? (c.low ? Math.min(...vals) : Math.max(...vals)) : null];
    }),
  );

  return (
    <div className="table-wrap">
      <table className="board rank">
        <thead>
          <tr>
            {ranked ? <th className="num">#</th> : null}
            <th>Temsilci</th>
            {COLS.map((c) => (
              <th key={c.key} className="num" title={c.title}>
                {ranked ? (
                  <button className={`sort-btn${sort === c.key ? " on" : ""}`} onClick={() => setSort(c.key)}>
                    {c.label}
                    {sort === c.key ? (c.low ? " ▲" : " ▼") : ""}
                  </button>
                ) : (
                  c.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.rep_id}>
              {ranked ? <td className="num rank-no">{i + 1}</td> : null}
              <td className="name">{r.name}</td>
              {COLS.map((c) => {
                const v = read(r, c.key);
                const top = ranked && v !== null && best.get(c.key) === v;
                return (
                  <td key={c.key} className={`num${top ? " best" : ""}${sort === c.key ? " sorted" : ""}`}>
                    {c.fmt(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            {ranked ? <td /> : null}
            <td className="name">Ekip ortalaması</td>
            {COLS.map((c) => (
              <td key={c.key} className="num">
                {c.fmt(read(data.avg, c.key))}
              </td>
            ))}
          </tr>
          {ranked ? (
            <tr>
              <td />
              <td className="name">Ekip toplamı</td>
              {COLS.map((c) => (
                <td key={c.key} className="num">
                  {c.fmt(read(data.team, c.key))}
                </td>
              ))}
            </tr>
          ) : null}
        </tfoot>
      </table>
    </div>
  );
}

/** Seçilen dönemde temsilcilerin ciro payı, büyükten küçüğe. */
function CiroShare({ reps, total }: { reps: ReportRow[]; total: number }) {
  const rows = [...reps].filter((r) => r.ciro > 0).sort((x, y) => y.ciro - x.ciro);
  if (!rows.length || total <= 0) return null;
  const max = rows[0].ciro;
  return (
    <div className="ciro-share">
      <div className="ciro-share-head">Dönem cirosu · temsilci payı</div>
      {rows.map((r, i) => (
        <div key={r.rep_id} className="cs-row" title={`${r.name}: ${tl(r.ciro)} · ${int(r.satis)} satış`}>
          <span className="cs-rank">{i + 1}</span>
          <span className="cs-name">{r.name}</span>
          <div className="cs-track">
            <div className="cs-bar" style={{ width: `${(r.ciro / max) * 100}%` }} />
          </div>
          <span className="cs-val tabular">
            {tlShort(r.ciro)} <span className="muted">%{num((100 * r.ciro) / total)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

// --- aylık ciro --------------------------------------------------------------

const W = 440;
const H = 220;
const PAD = { top: 16, right: 12, bottom: 28, left: 56 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

function CiroChart({ months }: { months: CiroMonth[] }) {
  const shown = months.filter((m, i) => m.ciro > 0 || months.slice(0, i).some((x) => x.ciro > 0));
  const [hover, setHover] = useState<number | null>(null);
  if (!shown.length) return <div className="empty">Bu dönemde satış yok.</div>;
  const max = niceMax(Math.max(...shown.map((m) => m.ciro)));
  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const slot = iw / shown.length;
  const bw = Math.min(44, slot * 0.6);
  const y = (v: number) => PAD.top + ih - (v / max) * ih;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * max);
  const h = hover === null ? null : shown[hover];

  return (
    <div className="chart ciro-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Aylık ekip cirosu" onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} className="gridline" />
            <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="tick">
              {tlShort(t).replace(" TL", "")}
            </text>
          </g>
        ))}
        {shown.map((m, i) => {
          const x = PAD.left + slot * i + (slot - bw) / 2;
          const top = y(m.ciro);
          const height = PAD.top + ih - top;
          return (
            <g key={m.ay} onMouseEnter={() => setHover(i)}>
              <rect x={PAD.left + slot * i} y={PAD.top} width={slot} height={ih} fill="transparent" />
              <path
                className={`ciro-bar${hover === i ? " on" : ""}`}
                d={
                  height > 4
                    ? `M${x},${top + height} V${top + 4} Q${x},${top} ${x + 4},${top} H${x + bw - 4} Q${x + bw},${top} ${x + bw},${top + 4} V${top + height} Z`
                    : `M${x},${top + height} V${top} H${x + bw} V${top + height} Z`
                }
              />
              <text x={x + bw / 2} y={H - 8} textAnchor="middle" className="tick">
                {m.ay_etiket.split(" ")[0].slice(0, 3)}
              </text>
            </g>
          );
        })}
      </svg>
      {h ? (
        <div
          className="tooltip"
          style={{
            left: `${((PAD.left + slot * (hover ?? 0) + slot / 2) / W) * 100}%`,
            top: `${(y(h.ciro) / H) * 100}%`,
          }}
        >
          <b>{h.ay_etiket}</b>
          <div>{tl(h.ciro)}</div>
          <div className="muted">
            {int(h.satis)} satış · ort. {tl(h.ortalama)}
          </div>
        </div>
      ) : null}
    </div>
  );
}
