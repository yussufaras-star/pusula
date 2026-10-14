import { useMemo, useState } from "react";

import { usePolling } from "../api";
import { int, pct, tl, tlShort } from "../format";
import type { ReportRow, SourceYieldPayload, SourceYieldRow } from "../types";

type Dim = "lead_source" | "utm_source" | "utm_campaign" | "utm_content";

const DIMS: { key: Dim; label: string }[] = [
  { key: "lead_source", label: "Lead kaynağı" },
  { key: "utm_source", label: "Reklam kaynağı" },
  { key: "utm_campaign", label: "Kampanya" },
  { key: "utm_content", label: "Reklam içeriği" },
];

/** Bundan az lead'i olan kaynağın oranı gürültülü; karşılaştırmaya girmez. */
const MIN_LEAD = 20;
const EMPTY = "(boş)";

type SortKey = "lead" | "islenme" | "ulasma" | "satis_orani" | "ciro" | "lead_basi_ciro";

interface Col {
  key: SortKey;
  label: string;
  title: string;
}

const COLS: Col[] = [
  { key: "lead", label: "Lead", title: "Dönemde atanan lead" },
  { key: "islenme", label: "İşlenen", title: "Statüsü 'Yeni Müşteri Adayı'ndan çıkmış lead / atanan" },
  { key: "ulasma", label: "Ulaşılan", title: "Ulaşılmış statüdeki lead / işlenen" },
  { key: "satis_orani", label: "Satışa dönüş", title: "Atamadan sonra satın alan lead / atanan" },
  { key: "ciro", label: "Ciro", title: "Bu lead'lerin atamadan sonraki kazanılmış satışları (bugüne kadar)" },
  { key: "lead_basi_ciro", label: "Lead başı ciro", title: "Ciro / atanan lead: kaynağın değeri" },
];

const name = (r: SourceYieldRow) => r.source ?? EMPTY;

function Insights({ rows, total }: { rows: SourceYieldRow[]; total: SourceYieldRow }) {
  const solid = rows.filter((r) => r.lead >= MIN_LEAD && r.source !== null);
  if (solid.length < 2) return null;
  const byValue = [...solid].sort((a, b) => (b.lead_basi_ciro ?? 0) - (a.lead_basi_ciro ?? 0));
  const best = byValue[0];
  const worst = byValue[byValue.length - 1];
  const idle = [...solid].sort((a, b) => b.dokunulmamis - a.dokunulmamis)[0];
  return (
    <ul className="sy-insights">
      <li>
        En değerli: <b>{name(best)}</b> lead başı {tlShort(best.lead_basi_ciro)} (ortalama {tlShort(total.lead_basi_ciro)}),
        satışa dönüş {pct(best.satis_orani)}.
      </li>
      {worst !== best ? (
        <li>
          En zayıf: <b>{name(worst)}</b> lead başı {tlShort(worst.lead_basi_ciro)}, {int(worst.lead)} lead.
        </li>
      ) : null}
      {idle.dokunulmamis > 0 ? (
        <li>
          En çok bekleyen: <b>{name(idle)}</b> kaynağında {int(idle.dokunulmamis)} lead hiç işlenmemiş (
          {pct(100 - (idle.islenme ?? 0))}).
        </li>
      ) : null}
    </ul>
  );
}

/** Kaynak verimi: dönemde atanan lead'ler hangi kaynaktan ve ne kadar satışa döndü. */
export function SourceYield({
  start,
  end,
  reps,
  isAdmin,
}: {
  start: string;
  end: string;
  reps: ReportRow[];
  isAdmin: boolean;
}) {
  const [dim, setDim] = useState<Dim>("lead_source");
  const [rep, setRep] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("lead_basi_ciro");
  const q = new URLSearchParams({ dim, start, end });
  if (rep) q.set("rep_id", rep);
  const { data, error } = usePolling<SourceYieldPayload>(`/api/source-yield?${q}`, 0);

  const rows = useMemo(() => {
    const list = [...(data?.rows ?? [])];
    // Az lead'li kaynaklar oran sıralamasında alta.
    return list.sort((a, b) => {
      const sa = a.lead >= MIN_LEAD ? 1 : 0;
      const sb = b.lead >= MIN_LEAD ? 1 : 0;
      if (sort !== "lead" && sort !== "ciro" && sa !== sb) return sb - sa;
      return (b[sort] ?? -1) - (a[sort] ?? -1);
    });
  }, [data, sort]);

  const avg = data?.total?.lead_basi_ciro ?? null;
  const maxValue = Math.max(0, ...rows.filter((r) => r.lead >= MIN_LEAD).map((r) => r.lead_basi_ciro ?? 0));

  return (
    <>
      <div className="ss-controls">
        <div className="chips" role="group" aria-label="Kaynak alanı">
          {DIMS.map((d) => (
            <button key={d.key} className={`chip${dim === d.key ? " on" : ""}`} onClick={() => setDim(d.key)} aria-pressed={dim === d.key}>
              {d.label}
            </button>
          ))}
        </div>
        {isAdmin ? (
          <select className="ss-rep" value={rep} onChange={(e) => setRep(e.target.value)} aria-label="Temsilci">
            <option value="">Tüm ekip</option>
            {reps.map((r) => (
              <option key={r.rep_id} value={r.rep_id}>
                {r.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {error ? <div className="banner error">Kaynak verimi alınamadı: {error.message}</div> : null}
      {data && !data.available ? (
        <div className="empty">Reklam kaynağı verisi ilk saatlik güncellemeyle gelecek.</div>
      ) : !data ? (
        <div className="skeleton" />
      ) : !rows.length ? (
        <div className="empty">Bu dönemde atanan lead yok.</div>
      ) : (
        <>
          {data.total ? <Insights rows={rows} total={data.total} /> : null}
          <div className="table-wrap">
            <table className="board sy-table">
              <thead>
                <tr>
                  <th>{data.label}</th>
                  {COLS.map((c) => (
                    <th key={c.key} className="num" title={c.title}>
                      <button className={`sort-btn${sort === c.key ? " on" : ""}`} onClick={() => setSort(c.key)}>
                        {c.label}
                        {sort === c.key ? " ▼" : ""}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const thin = r.lead < MIN_LEAD;
                  const v = r.lead_basi_ciro;
                  const tone = thin || v === null || avg === null ? "" : v >= avg * 1.2 ? " good" : v <= avg * 0.8 ? " critical" : "";
                  return (
                    <tr key={name(r)} className={thin ? "sy-thin" : undefined}>
                      <td className="name" title={name(r)}>
                        {name(r).length > 34 ? `${name(r).slice(0, 33)}…` : name(r)}
                        {thin ? <span className="sub"> az veri</span> : null}
                      </td>
                      <td className="num">{int(r.lead)}</td>
                      <td className="num" title={`${int(r.dokunulmamis)} lead hiç işlenmemiş`}>
                        {pct(r.islenme)}
                        {r.dokunulmamis ? <span className="sub"> {int(r.dokunulmamis)} bekliyor</span> : null}
                      </td>
                      <td className="num" title={`${int(r.ulasildi)} ulaşıldı · ${int(r.ulasilamadi)} ulaşılamadı`}>
                        {pct(r.ulasma)}
                      </td>
                      <td className="num" title={`${int(r.satan_lead)} lead satın aldı · ${int(r.satis)} satış`}>
                        {pct(r.satis_orani)}
                      </td>
                      <td className="num">{r.ciro ? tlShort(r.ciro) : <span className="muted">—</span>}</td>
                      <td className={`num sy-value${tone}`} title={v === null ? undefined : tl(v)}>
                        <span className="sy-bar" style={{ width: `${maxValue && v && !thin ? (v / maxValue) * 100 : 0}%` }} />
                        <span className="sy-num">{v ? tlShort(v) : "—"}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              {data.total ? (
                <tfoot>
                  <tr>
                    <td className="name">Toplam</td>
                    <td className="num">{int(data.total.lead)}</td>
                    <td className="num">{pct(data.total.islenme)}</td>
                    <td className="num">{pct(data.total.ulasma)}</td>
                    <td className="num">{pct(data.total.satis_orani)}</td>
                    <td className="num">{tlShort(data.total.ciro)}</td>
                    <td className="num">{tlShort(data.total.lead_basi_ciro)}</td>
                  </tr>
                </tfoot>
              ) : null}
            </table>
          </div>
        </>
      )}
      <p className="muted fn-note">
        Seçili dönemde atanan lead'ler (bugünkü statüleriyle). İşlenen: "Yeni Müşteri Adayı"ndan çıkmış. Ulaşılan:
        ulaşılamadı/aging dışındaki statüler, işlenenlere oranla. Satış: lead'in müşteri kaydına bağlı, atamadan sonra kapanan
        kazanılmış satışlar; kısa dönemlerde satış henüz gelmemiş olabilir, kaynak kıyası için 90 gün daha sağlıklı. Yeşil/kırmızı:
        lead başı ciro ortalamanın %20 üstü/altı. {MIN_LEAD} lead altı kaynaklar "az veri" (kıyasa girmez). Reklam alanları
        lead'lerin yaklaşık %60'ında dolu.
      </p>
    </>
  );
}
