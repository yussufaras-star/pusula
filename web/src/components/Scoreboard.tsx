import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { ShareButton } from "./ShareButton";
import type { ScorePeriod, ScoreRow, Scoreboard as Board } from "../types";

type Order = "gelisim" | "puan";

const PERIODS: [ScorePeriod, string][] = [
  ["hafta", "Haftalık"],
  ["ay", "Aylık"],
  ["ceyrek", "Çeyreklik"],
];
const UNIT: Record<ScorePeriod, string> = { hafta: "Haftanın", ay: "Ayın", ceyrek: "Çeyreğin" };

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const d = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));
const nf2 = new Intl.NumberFormat("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** İşaretli puan farkı: +0,18 / −0,05; 0,05'ten küçük fark nötr. */
function Delta({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="sb-delta muted">—</span>;
  const tone = v >= 0.05 ? "good" : v <= -0.05 ? "critical" : "neutral";
  const text = `${v > 0 ? "+" : v < 0 ? "−" : "±"}${nf2.format(Math.abs(v))}`;
  return <span className={`sb-delta chg ${tone}`}>{text}</span>;
}

const names = (xs: string[]) => (xs.length ? xs.join(", ") : "—");

/** Puan tablosu: SONAR-41 beş boyutu, seçilen haftayı içeren hafta, ay ya da çeyrek;
 * gelişime ya da puana göre sıralı. Boyut liderleri ayrı kart; ikisi de Cliq'e ayrı gider. Yalnız yönetici. */
export function Scoreboard({ week }: { week: string }) {
  const [order, setOrder] = useState<Order>("gelisim");
  const [period, setPeriod] = useState<ScorePeriod>("hafta");
  const { data, error } = usePolling<Board>(`/api/conversations/scoreboard?week=${week}&donem=${period}`, 0);

  const periodChips = (
    <div className="chips" role="group" aria-label="Dönem" data-share-skip>
      {PERIODS.map(([p, label]) => (
        <button key={p} className={`chip${period === p ? " on" : ""}`} onClick={() => setPeriod(p)} aria-pressed={period === p}>
          {label}
        </button>
      ))}
    </div>
  );

  if (error) return <div className="banner error">Puan tablosu alınamadı: {error.message}</div>;
  if (!data || data.donem !== period || data.bas > week || data.son < week) return <div className="skeleton" />;
  const dims = Object.keys(data.score_labels);
  const rank = (r: ScoreRow) => (order === "gelisim" ? r.sira_gelisim : r.sira_puan);
  const rows = [...data.temsilciler].sort((a, b) => {
    const x = rank(a) ?? 99;
    const y = rank(b) ?? 99;
    return x - y || (b.ort ?? 0) - (a.ort ?? 0) || a.name.localeCompare(b.name, "tr");
  });
  const byName = new Map(data.temsilciler.map((r) => [r.name, r]));
  const top = byName.get(data.en_cok_gelisen[0]);
  const ranked = rows.some((r) => r.yeterli);
  const span = data.donem === "hafta" ? data.etiket : `${data.etiket} · ${d(data.bas)} – ${d(data.son)}`;
  const unit = UNIT[data.donem];

  return (
    <>
      <section className="card" data-share="boyut-liderleri">
        <div className="card-head">
          <h2>Boyut liderleri</h2>
          <span className="hint">
            {span} · gelişim: {data.kiyas_etiket} ortalamasına göre
          </span>
          {periodChips}
          <ShareButton kart="boyut-liderleri" baslik={`Boyut liderleri · ${data.etiket}`} />
        </div>
        <div className="card-body">
          {ranked ? (
            <div className="sb-highlights">
              <div className="sb-hero">
                <div className="sb-hero-label">{unit} en çok gelişeni</div>
                <div className="sb-hero-name">{names(data.en_cok_gelisen)}</div>
                {top ? (
                  <div className="muted">
                    ortalama {num(top.onceki_ort)} → {num(top.ort)} <Delta v={top.gelisim_ort} />
                  </div>
                ) : (
                  <div className="muted">bu dönem ortalamasını yükselten yok</div>
                )}
              </div>
              {dims.map((k) => {
                const lead = data.liderler[k]?.puan ?? [];
                const grow = data.liderler[k]?.gelisim ?? [];
                const leadScore = byName.get(lead[0])?.puanlar[k];
                const growBy = byName.get(grow[0])?.gelisim[k];
                return (
                  <div key={k} className="sb-dim" title={data.score_labels[k]}>
                    <div className="sb-dim-head">
                      <b>{k}</b> {data.score_labels[k]}
                    </div>
                    <div className="sb-dim-label">Lider</div>
                    <div className="sb-dim-name">
                      {names(lead)} {lead.length ? <span className="muted">{num(leadScore)}</span> : null}
                    </div>
                    <div className="sb-dim-label">En çok gelişen</div>
                    <div className="sb-dim-name">
                      {names(grow)} {grow.length ? <Delta v={growBy} /> : null}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="empty">Bu dönem liderlik için yeterli analiz yok (temsilci başına en az {data.min_gorusme} görüşme).</div>
          )}
          <p className="muted fn-note">
            Lider: boyutta en yüksek puan. En çok gelişen: {data.kiyas_etiket} ortalamasına göre en çok yükselen. Dönemde en az{" "}
            {data.min_gorusme} analizli görüşmesi olanlar sayılır; 0,05'ten küçük farklar eşit sayılır.
          </p>
        </div>
      </section>

      <section className="card" data-share="puan-tablosu">
        <div className="card-head">
          <h2>Puan tablosu</h2>
          <span className="hint">
            {span} · gelişim: {data.kiyas_etiket} ortalamasına göre
          </span>
          {periodChips}
          <div className="chips" role="group" aria-label="Sıralama" data-share-skip>
            {(["gelisim", "puan"] as const).map((o) => (
              <button key={o} className={`chip${order === o ? " on" : ""}`} onClick={() => setOrder(o)} aria-pressed={order === o}>
                {o === "gelisim" ? "Gelişime göre" : "Puana göre"}
              </button>
            ))}
          </div>
          <ShareButton kart="puan-tablosu" baslik={`Puan tablosu · ${data.etiket} · ${order === "gelisim" ? "gelişim" : "puan"}`} />
        </div>
        <div className="card-body">
          {ranked ? (
            <div className="table-wrap">
              <table className="board sb-table">
                <thead>
                  <tr>
                    <th className="num">#</th>
                    <th>Temsilci</th>
                    <th className="num" title="Dönemde analiz edilen görüşme">Görüşme</th>
                    {dims.map((k) => (
                      <th key={k} className="num" title={data.score_labels[k]}>
                        {k}
                      </th>
                    ))}
                    <th className={`num${order === "puan" ? " sorted" : ""}`}>Ortalama</th>
                    <th className={`num${order === "gelisim" ? " sorted" : ""}`} title={`${data.kiyas_etiket} ortalamasına göre`}>
                      Gelişim
                    </th>
                    <th title="En çok ilerlediği / en düşük kaldığı boyut">Güçlü · odak</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.rep_id} className={r.yeterli ? "" : "muted"}>
                      <td className="num rank-no">{rank(r) ?? "—"}</td>
                      <td className="name">{r.name}</td>
                      <td className="num">{int(r.n)}</td>
                      {dims.map((k) => (
                        <td key={k} className="num" title={`önceki ${num(r.onceki_puanlar[k])}`}>
                          <div>{num(r.puanlar[k])}</div>
                          {r.yeterli ? <Delta v={r.gelisim[k]} /> : null}
                        </td>
                      ))}
                      <td className={`num${order === "puan" ? " sorted" : ""}`}>{num(r.ort)}</td>
                      <td className={`num${order === "gelisim" ? " sorted" : ""}`}>
                        {r.yeterli ? (
                          r.kiyas_var ? <Delta v={r.gelisim_ort} /> : <span className="muted">kıyas yok</span>
                        ) : (
                          <span className="muted" title={`En az ${data.min_gorusme} analizli görüşme gerekir`}>
                            yetersiz veri
                          </span>
                        )}
                      </td>
                      <td className="sb-focus">
                        {r.en_iyi_gelisim ? <span className="chg good">▲ {r.en_iyi_gelisim}</span> : null}
                        {r.odak ? <span className="muted"> · odak {r.odak}</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td />
                    <td className="name">Ekip</td>
                    <td className="num">{int(data.ekip.n)}</td>
                    {dims.map((k) => (
                      <td key={k} className="num">
                        <div>{num(data.ekip.puanlar[k])}</div>
                        <Delta v={data.ekip.gelisim[k]} />
                      </td>
                    ))}
                    <td className="num">{num(data.ekip.ort)}</td>
                    <td className="num">
                      <Delta v={data.ekip.gelisim_ort} />
                    </td>
                    <td />
                  </tr>
                </tfoot>
              </table>
            </div>
          ) : (
            <div className="empty">Bu dönem sıralamaya yetecek analiz yok (temsilci başına en az {data.min_gorusme} görüşme).</div>
          )}
          <p className="muted fn-note">
            Puanlar 1-5; görüşmenin boyut puanı o boyutta puanlanan davranışların ortalamasıdır. Gelişim, dönemin puanından
            temsilcinin {data.kiyas_etiket} içindeki ({d(data.kiyas_bas)}'dan) görüşmelerinin ortalamasını çıkarır; böylece
            puanı düşük olan da en çok gelişen olabilir. Dönemde {data.min_gorusme} görüşmeden azı "yetersiz veri" sayılır,
            sıralamaya ve liderliğe girmez. 0,05'ten küçük farklar eşit sayılır.
          </p>
        </div>
      </section>
    </>
  );
}
