import { useEffect, useState } from "react";

import { usePolling } from "../api";
import { int, num, tl } from "../format";
import type { CommissionPayload, CommissionRep } from "../types";

const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric", timeZone: "UTC" });

function monthParam(offset: number): string {
  const now = new Date(new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date()));
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset, 1));
  return d.toISOString().slice(0, 7);
}

const pct = (v: number) => `%${num(v)}`;

/** Satış adedi çubuğu: kademe eşikleri, gerçekleşen ve ay sonu tahmini işareti. */
function TierBar({ rep, tiers, closed }: { rep: CommissionRep; tiers: CommissionPayload["tiers"]; closed: boolean }) {
  // Ölçek son eşiğin biraz ötesinde sabit; tahmin taşarsa işaret sonda kalır.
  const max = Math.max(Math.max(...tiers.map((t) => t.esik)) + 4, rep.sayilan + 2);
  const x = (v: number) => `${Math.min(v / max, 1) * 100}%`;
  return (
    <div
      className="cm-bar"
      title={`${int(rep.sayilan)} Premium/Plus satış${closed ? "" : ` · ay sonu tahmini ${num(rep.tahmin_sayilan)}`}`}
    >
      <div className="cm-fill" style={{ width: x(rep.sayilan) }} />
      {tiers
        .filter((t) => t.esik > 0)
        .map((t) => (
          <div key={t.esik} className="cm-tick" style={{ left: x(t.esik) }}>
            <span>{t.esik}</span>
          </div>
        ))}
      {!closed && rep.tahmin_sayilan > rep.sayilan ? (
        <div className="cm-forecast" style={{ left: x(rep.tahmin_sayilan) }} />
      ) : null}
    </div>
  );
}

function NextStep({ rep, closed }: { rep: CommissionRep; closed: boolean }) {
  if (!rep.sonraki) return <span className="cm-top">en üst kademe · {pct(rep.oran)}</span>;
  const s = rep.sonraki;
  return (
    <span>
      {pct(s.oran)} için <b>{int(s.kalan)}</b> satış
      {!closed && s.gunluk_gereken !== null ? <span className="muted"> · günde {num(s.gunluk_gereken)}</span> : null}
      {s.ek_prim > 0 ? <span className="muted"> · +{tl(s.ek_prim)} prim</span> : null}
    </span>
  );
}

/** syncTick: "Verileri güncelle" sonrası artar, kart beklemeden yenilenir. */
export function CommissionCard({ isAdmin, syncTick = 0 }: { isAdmin: boolean; syncTick?: number }) {
  const [offset, setOffset] = useState(0);
  const { data, error, refresh } = usePolling<CommissionPayload>(
    `/api/commission?month=${monthParam(offset)}`,
    5 * 60_000,
  );
  useEffect(() => {
    if (syncTick > 0) refresh();
  }, [syncTick, refresh]);
  const closed = offset < 0;
  const reps = [...(data?.reps ?? [])].sort((a, b) => b.sayilan - a.sayilan || b.ciro - a.ciro);

  return (
    <section className="card">
      <div className="card-head">
        <h2>Aylık prim hedefi{data ? ` · ${monthFmt.format(new Date(`${data.month}T00:00:00Z`))}` : ""}</h2>
        <div className="chips" role="group" aria-label="Ay">
          <button className={`chip${offset === 0 ? " on" : ""}`} onClick={() => setOffset(0)} aria-pressed={offset === 0}>
            Bu ay
          </button>
          <button className={`chip${offset === -1 ? " on" : ""}`} onClick={() => setOffset(-1)} aria-pressed={offset === -1}>
            Geçen ay
          </button>
        </div>
      </div>
      <div className="card-body">
        {error ? <div className="banner error">Prim verisi alınamadı: {error.message}</div> : null}
        {data && !data.available ? <div className="empty">Deal paket verisi ilk saatlik güncellemeyle gelecek.</div> : null}
        {data?.available ? (
          <>
            <div className="table-wrap">
              <table className="board cm-table">
                <thead>
                  <tr>
                    {isAdmin ? <th>Temsilci</th> : null}
                    <th className="cm-bar-col">
                      Premium / Plus satış{" "}
                      <span className="cm-legend">
                        {data.tiers
                          .filter((t) => t.esik > 0)
                          .map((t) => `${t.esik} → ${pct(t.oran)}`)
                          .join(" · ")}
                      </span>
                    </th>
                    <th className="num">Satış</th>
                    <th className="num">Ciro (tüm satış)</th>
                    <th className="num">Oran</th>
                    <th className="num">Prim</th>
                    {closed ? null : <th className="num">Ay sonu tahmini</th>}
                    <th>%5 hedefi</th>
                  </tr>
                </thead>
                <tbody>
                  {reps.map((r) => (
                    <tr key={r.rep_id}>
                      {isAdmin ? <td className="name">{r.name}</td> : null}
                      <td className="cm-bar-col">
                        <div className="cm-count">
                          <b>{int(r.sayilan)}</b>
                          {closed ? null : <span className="muted"> · tahmin {num(r.tahmin_sayilan)}</span>}
                        </div>
                        <TierBar rep={r} tiers={data.tiers} closed={closed} />
                      </td>
                      <td className="num">
                        {int(r.toplam_satis)}
                        {r.diger_satis ? <div className="sub">{int(r.diger_satis)} diğer paket</div> : null}
                      </td>
                      <td className="num">
                        {tl(r.ciro)}
                        <div className="sub">
                          Premium/Plus {tl(r.ciro_sayilan)}
                          {r.ciro_diger ? ` + diğer ${tl(r.ciro_diger)}` : ""}
                        </div>
                      </td>
                      <td className="num">{pct(r.oran)}</td>
                      <td className="num cm-prim">{tl(r.prim)}</td>
                      {closed ? null : (
                        <td className="num">
                          {tl(r.tahmin_prim)}
                          <div className="sub">
                            {pct(r.tahmin_oran)} · {tl(r.tahmin_ciro)} ciro
                          </div>
                        </td>
                      )}
                      <td>
                        <NextStep rep={r} closed={closed} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="cm-key" aria-label="Çubuk nasıl okunur">
              <span>
                <span className="cm-key-fill" /> Mavi dolu kısım: bu ay gerçekleşen Premium/Plus satış
              </span>
              <span>
                <span className="cm-key-tick" /> Koyu dik çizgiler: kademe eşikleri (28 → %3, 30 → %5)
              </span>
              {closed ? null : (
                <span>
                  <span className="cm-key-dash" /> Kesikli çizgi: bu hızla ay sonunda ulaşılacak satış (çubuğun sonundaysa ölçeğin
                  ötesinde)
                </span>
              )}
            </div>
            <p className="muted fn-note">
              Sayılan: ay içinde kazanılan {data.packages.join(", ")}. Ciro: o ay "Kapandı Kazanıldı" olan tüm satışlar
              (Kamp ve diğer paketler dahil). Oran: 27'ye kadar %2,5 · 28–29 %3 · 30 ve üzeri %5;
              prim o ay kazanılan tüm satışların cirosu üzerinden. Tahmin: iş günü başına hız × ayın {data.month_workdays} iş
              günü (geçen {num(data.elapsed_workdays)}). "%5 hedefi": 30 satışa kalan, günlük gereken ve %5'e çıkınca bugünkü ciroyla oluşacak prim farkı.
            </p>
          </>
        ) : null}
      </div>
    </section>
  );
}
