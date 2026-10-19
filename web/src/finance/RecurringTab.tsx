import { useState } from "react";

import { StatTile } from "../components/StatTile";
import { day, short, usd } from "./format";
import type { RecurringPayload } from "./types";
import { categoryName, CompanyTag, FxNote, Loading, useFin, type TabProps } from "./ui";

// Abonelik sayılan kategoriler (yazılım, reklam); diğerleri düzenli ödeme
const SUBSCRIPTION = new Set(["yazilim", "reklam"]);

export function RecurringTab({ scope, start, end, meta, tick }: TabProps) {
  const [group, setGroup] = useState<string | null>(null);
  const { data, error } = useFin<RecurringPayload>(`/api/finance/recurring?company=${scope}&start=${start}&end=${end}`, tick);
  if (!data) return <Loading error={error} />;

  const items = data.items.filter((i) => !group || (i.category_id ?? "_none") === group);
  const subs = data.items.filter((i) => i.category_id && SUBSCRIPTION.has(i.category_id));
  const subsMonthly = subs.reduce((s, i) => s + i.monthly_usd, 0);
  const stopped = data.items.filter((i) => i.stopped).length;

  return (
    <>
      <FxNote missing={data.missing_fx} />
      <div className="tiles fin-tiles">
        <StatTile label="Aylık düzenli ödeme" value={short(data.monthly_usd)} title={usd(data.monthly_usd)} expected={`${data.items.length} karşı taraf`} />
        <StatTile label="Yıllık karşılığı" value={short(data.monthly_usd * 12)} title={usd(data.monthly_usd * 12)} expected="aynı hızla" />
        <StatTile label="Abonelikler" value={short(subsMonthly)} title={usd(subsMonthly)} expected={`${subs.length} yazılım ve reklam, aylık`} />
        <StatTile
          label="Durmuş görünen"
          value={stopped}
          tone={stopped ? "warning" : "neutral"}
          expected="beklenen ödeme bir haftadan fazla gecikti"
        />
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Düzenli ödemeler</h2>
          <span className="muted">
            {day(data.start)} – {day(data.end)}
          </span>
        </div>
        <div className="card-body">
          <p className="muted fin-note">
            Aynı karşı tarafa en az iki farklı ayda yapılan ödemeler. Aylık tutar, verinin başladığı günden bu yana ortalamadır. Hesaplar
            arası aktarımlar ve satıcı cüzdan hareketleri hariç. Kategoriyi Nakit akışı'ndan düzeltirsen burada da değişir.
          </p>
          <div className="chips" role="group" aria-label="Kategori">
            <button className={`chip${group === null ? " on" : ""}`} aria-pressed={group === null} onClick={() => setGroup(null)}>
              Tümü · {short(data.monthly_usd)}/ay
            </button>
            {data.groups.map((g) => (
              <button key={g.id} className={`chip${group === g.id ? " on" : ""}`} aria-pressed={group === g.id} onClick={() => setGroup(group === g.id ? null : g.id)}>
                {g.name} · {short(g.monthly_usd)}/ay
              </button>
            ))}
          </div>
          {items.length ? (
            <div className="table-wrap">
              <table className="board fin-table">
                <thead>
                  <tr>
                    <th>Karşı taraf</th>
                    <th>Kategori</th>
                    <th>Sıklık</th>
                    <th className="num">Ödeme</th>
                    <th className="num">Tek ödeme</th>
                    <th className="num">Aylık</th>
                    <th className="num">Yıllık</th>
                    <th>Son</th>
                    <th>Sonraki</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.key}>
                      <td className="fin-desc">
                        <b>{i.label}</b>
                        {scope === "all" && i.company_id ? <CompanyTag id={i.company_id} /> : null}
                        {i.rising ? <span className="badge warning">arttı</span> : null}
                        {i.stopped ? <span className="badge">durmuş olabilir</span> : null}
                        <div className="sub">{i.accounts.join(", ")}</div>
                      </td>
                      <td>{categoryName(meta, i.category_id)}</td>
                      <td>{i.cadence}</td>
                      <td className="num">{i.count}</td>
                      <td className="num">
                        {usd(i.median_usd)}
                        {i.rising ? <div className="sub">son {usd(i.last_usd)}</div> : null}
                      </td>
                      <td className="num">
                        <b>{usd(i.monthly_usd)}</b>
                      </td>
                      <td className="num">{usd(i.yearly_usd)}</td>
                      <td>{day(i.last)}</td>
                      <td>{day(i.next_due)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Bu aralıkta düzenli ödeme yok.</div>
          )}
        </div>
      </section>
    </>
  );
}
