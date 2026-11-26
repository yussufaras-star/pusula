import { Fragment, useState } from "react";

import { usePolling } from "../api";
import { int, pct } from "../format";
import { StatTile } from "./StatTile";

type Durum = "aldi" | "almadi" | "surecte";

interface Kisi {
  ad: string;
  kamp_gun: string;
  premium_gun: string | null;
  gun: number | null;
  durum: Durum;
  neden: string;
}

interface Counts {
  giden: number;
  aldi: number;
  almadi: number;
  surecte: number;
}

interface Rep extends Counts {
  rep_id: string;
  name: string;
  kisiler: Kisi[];
}

interface KampPremiumPayload {
  bas: string;
  bekleme_gun: number;
  temsilciler: Rep[];
  toplam: Counts;
  oran: number | null;
  ort_gun: number | null;
}

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const day = (iso: string | null) => (iso ? dayFmt.format(new Date(iso)) : "—");
const DURUM: Record<Durum, string> = { aldi: "Premium aldı", almadi: "Almadı", surecte: "Süreçte" };
const cell = (v: number) => (v ? int(v) : <span className="muted">—</span>);

/** Kamp → Premium: lead'den kişiye Kamp ile dönüp sonra Premium alanlar; satıra tıkla, kişiler açılır. */
export function KampPremium() {
  const [open, setOpen] = useState<string | null>(null);
  const { data, error } = usePolling<KampPremiumPayload>("/api/kamp-premium", 0);
  if (error) return <div className="banner error">Kamp dönüşümü alınamadı: {error.message}</div>;
  if (!data) return <div className="skeleton" />;
  if (!data.temsilciler.length) return <div className="empty">Son 5 ayda Kamp ile kişiye dönen yok.</div>;

  const t = data.toplam;
  return (
    <>
      <div className="tiles">
        <StatTile
          label="Toplu dönüşüm"
          value={data.oran === null ? "—" : pct(data.oran * 100)}
          expected={`${int(t.aldi)} / ${int(t.aldi + t.almadi)} kişi Premium aldı`}
        />
        <StatTile label="Kamp ile kişiye dönen" value={int(t.giden)} expected={`${int(t.surecte)} kişi süreçte`} />
        <StatTile
          label="Kamptan Premium'a"
          value={data.ort_gun === null ? "—" : `${int(data.ort_gun)} gün`}
          expected="ortalama süre"
        />
      </div>

      <div className="table-wrap">
        <table className="board meet-board">
          <thead>
            <tr>
              <th>Temsilci</th>
              <th className="num">Kamp ile dönen</th>
              <th className="num">Sonra Premium alan</th>
              <th className="num">Premium almayan</th>
              <th className="num" title={`Kamp satışından ${data.bekleme_gun} gün geçmedi`}>
                Süreçte
              </th>
            </tr>
          </thead>
          <tbody>
            {data.temsilciler.map((r) => {
              const isOpen = open === r.rep_id;
              return (
                <Fragment key={r.rep_id}>
                  <tr className="clickable" onClick={() => setOpen(isOpen ? null : r.rep_id)} aria-expanded={isOpen}>
                    <td>
                      <div className="name">
                        <span className="caret" aria-hidden>
                          {isOpen ? "▾" : "▸"}
                        </span>{" "}
                        {r.name}
                      </div>
                    </td>
                    <td className="num">{cell(r.giden)}</td>
                    <td className="num">{cell(r.aldi)}</td>
                    <td className={`num ${r.almadi ? "warn-text" : ""}`}>{cell(r.almadi)}</td>
                    <td className="num">{cell(r.surecte)}</td>
                  </tr>
                  {isOpen ? (
                    <tr className="detail">
                      <td colSpan={5}>
                        <table className="board">
                          <thead>
                            <tr>
                              <th>Kişi</th>
                              <th>Kamp</th>
                              <th>Premium</th>
                              <th>Durum</th>
                              <th>Satın almama nedeni</th>
                            </tr>
                          </thead>
                          <tbody>
                            {r.kisiler.map((k, i) => (
                              <tr key={i}>
                                <td>{k.ad}</td>
                                <td>{day(k.kamp_gun)}</td>
                                <td>
                                  {day(k.premium_gun)}
                                  {k.gun !== null ? <div className="sub">{int(k.gun)} gün sonra</div> : null}
                                </td>
                                <td className={k.durum === "almadi" ? "warn-text" : k.durum === "aldi" ? "good-text" : "muted"}>
                                  {DURUM[k.durum]}
                                </td>
                                <td>{k.neden || <span className="muted">—</span>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td>
                <b>Toplam</b>
              </td>
              <td className="num">
                <b>{int(t.giden)}</b>
              </td>
              <td className="num">
                <b>{int(t.aldi)}</b>
              </td>
              <td className="num">
                <b>{int(t.almadi)}</b>
              </td>
              <td className="num">
                <b>{int(t.surecte)}</b>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Son 5 ayda kişiye dönüştürülen ve ilk satın aldığı paket Kamp olanlar; Premium: ondan sonra kazanılan Premium
        içeren paket. Premium ile gelip Kamp'ı sonra alanlar ve Premium-Kamp paketi sayılmaz. Kamp satışından{" "}
        {data.bekleme_gun} gün geçmeyen "süreçte"dir, orana girmez. Neden: kamptan sonraki görüşme itirazı ya da arama
        sayısı, yanında CRM statüsü.
      </p>
    </>
  );
}
