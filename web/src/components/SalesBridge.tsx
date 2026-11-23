import { usePolling } from "../api";
import { duration, int, minutes, num, pct, tl, tlShort } from "../format";
import { ShareButton } from "./ShareButton";

interface Factor {
  key: string;
  label: string;
  kind: "gün" | "lead/gün" | "oran" | "tl";
  a: number | null;
  b: number | null;
  degisim: number | null;
  etki: number | null;
}

interface Mover {
  rep_id: string;
  name: string;
  ciro_a: number;
  ciro_b: number;
  fark: number;
  satis_a: number;
  satis_b: number;
  donusum_a: number | null;
  donusum_b: number | null;
  sure_a: number | null;
  sure_b: number | null;
}

interface Quality {
  key: string;
  label: string;
  kind: "sure" | "sure_toplam" | "adet" | "yuzde";
  a: number | null;
  b: number | null;
  degisim: number | null;
}

interface Mix {
  paket: string;
  adet_a: number;
  adet_b: number;
  ciro_a: number;
  ciro_b: number;
  ort_a: number | null;
  ort_b: number | null;
  fark: number;
}

interface BridgeSide {
  label: string;
  ciro: number;
  satis: number;
}

interface Bridge {
  a: BridgeSide;
  b: BridgeSide;
  fark: number;
  fark_orani: number | null;
  ayristi: boolean;
  ozet: string;
  tespitler: { ton: "neg" | "pos"; metin: string }[];
  oneriler: string[];
  factors: Factor[];
  movers: Mover[];
  paketler: Mix[];
  kalite: Quality[];
}

function showFactor(v: number | null, kind: Factor["kind"]): string {
  if (v === null) return "—";
  if (kind === "oran") return pct(v * 100);
  if (kind === "tl") return tl(v);
  if (kind === "gün") return int(v);
  return num(v);
}

function showQuality(v: number | null, kind: Quality["kind"]): string {
  if (v === null) return "—";
  if (kind === "sure") return duration(v);
  if (kind === "sure_toplam") return minutes(v / 60);
  if (kind === "yuzde") return pct(v);
  return int(v);
}

const signed = (v: number) => `${v < 0 ? "−" : "+"}${tlShort(Math.abs(v))}`;
const tone = (v: number | null) =>
  v === null || Math.abs(v) < 1e-9 ? "" : v < 0 ? "neg" : "pos";
const arrow = (v: number | null) =>
  v === null || Math.abs(v) < 1e-9 ? "" : v < 0 ? "▼ " : "▲ ";

/** Oransal değişim (−%33) ya da yüzde ölçüde puan farkı (−12 puan). */
function change(v: number | null, points = false): string {
  if (v === null) return "—";
  const sign = v < 0 ? "−" : "+";
  return points
    ? `${sign}${num(Math.abs(v))} puan`
    : `${sign}%${num(Math.abs(v * 100))}`;
}

/** Dönem karşılaştırmasında ciro farkı: tespitler, öneriler ve huni ayrıştırması.
 * Sütunlar eski → yeni (B → A) sırada, dönem adlarıyla. */
export function SalesBridge({
  query,
  isAdmin,
}: {
  query: string;
  isAdmin: boolean;
}) {
  const { data, error } = usePolling<Bridge>(`/api/sales-bridge?${query}`, 0);

  if (error)
    return (
      <div className="banner error">
        Ciro ayrıştırması alınamadı: {error.message}
      </div>
    );
  if (!data) return <div className="skeleton" style={{ height: 320 }} />;

  const { a, b } = data;
  const maxEffect = Math.max(
    1,
    ...data.factors.map((f) => Math.abs(f.etki ?? 0)),
  );
  const heroTone = tone(data.fark);

  return (
    <section className="card bridge" data-share>
      <div className="card-head">
        <div>
          <h2>Ciro farkının kaynağı</h2>
          <span className="hint">
            {b.label} → {a.label} · tamamlanmış günler
          </span>
        </div>
        {isAdmin ? (
          <ShareButton
            kart="ciro-kopru"
            baslik={`Ciro farkı · ${b.label} → ${a.label}`}
          />
        ) : null}
      </div>
      <div className="card-body">
        <div className={`bridge-hero ${heroTone}`}>
          <div>
            <div className="bridge-ozet">
              {arrow(data.fark)}
              {data.ozet}
            </div>
            <div className="bridge-ozet-sub">
              {b.label}: <b>{tl(b.ciro)}</b> → {a.label}: <b>{tl(a.ciro)}</b>
            </div>
          </div>
          <div className="bridge-hero-side">
            <div className="bridge-delta">{signed(data.fark)}</div>
            <div className="bridge-ozet-sub">
              {int(b.satis)} → {int(a.satis)} satış
            </div>
          </div>
        </div>

        <div className="bridge-cols">
          <div>
            <h3 className="bridge-sub">Tespitler</h3>
            <ul className="bridge-findings">
              {data.tespitler.map((t, i) => (
                <li key={i} className={t.ton}>
                  <span className="bridge-mark" aria-hidden>
                    {t.ton === "neg" ? "▼" : "▲"}
                  </span>
                  {t.metin}
                </li>
              ))}
            </ul>
          </div>
          {data.oneriler.length ? (
            <div className="bridge-advice">
              <h3 className="bridge-sub">Satışları artırmak için</h3>
              <ol>
                {data.oneriler.map((o, i) => (
                  <li key={i}>{o}</li>
                ))}
              </ol>
            </div>
          ) : null}
        </div>

        <h3 className="bridge-sub">Ciroyu oluşturan adımlar</h3>
        <div className="table-wrap">
          <table className="board bridge-table">
            <thead>
              <tr>
                <th>Adım</th>
                <th className="num">{b.label}</th>
                <th className="num">{a.label}</th>
                <th className="num">Değişim</th>
                <th className="effect">Ciroya etkisi</th>
              </tr>
            </thead>
            <tbody>
              {data.factors.map((f) => {
                const w =
                  f.etki === null ? 0 : (Math.abs(f.etki) / maxEffect) * 50;
                return (
                  <tr
                    key={f.key}
                    className={
                      f.etki !== null && f.etki < 0 ? "row-neg" : undefined
                    }
                  >
                    <td>{f.label}</td>
                    <td className="num">{showFactor(f.b, f.kind)}</td>
                    <td className="num">{showFactor(f.a, f.kind)}</td>
                    <td className={`num ${tone(f.degisim)}`}>
                      {arrow(f.degisim)}
                      {change(f.degisim)}
                    </td>
                    <td className="effect">
                      {f.etki === null ? (
                        "—"
                      ) : (
                        <div className="bridge-bar">
                          <span
                            className={tone(f.etki)}
                            style={
                              f.etki < 0
                                ? { right: "50%", width: `${w}%` }
                                : { left: "50%", width: `${w}%` }
                            }
                          />
                          <b className={tone(f.etki)}>{signed(f.etki)}</b>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="muted cmp-note">
          Ciro = iş günü × günlük aranan lead × ulaşma × satışa dönüş × ortalama
          satış. Etkiler toplanınca farkı verir.
          {data.ayristi
            ? ""
            : " Bir dönemde satış ya da arama olmadığı için etkiler ayrıştırılamadı."}
        </p>

        {data.paketler.length ? (
          <>
            <h3 className="bridge-sub">Paket karışımı</h3>
            <div className="table-wrap">
              <table className="board">
                <thead>
                  <tr>
                    <th>Paket</th>
                    <th className="num">Adet</th>
                    <th className="num">Ort. tutar</th>
                    <th className="num">Ciro {b.label}</th>
                    <th className="num">Ciro {a.label}</th>
                    <th className="num">Fark</th>
                  </tr>
                </thead>
                <tbody>
                  {data.paketler.map((p) => (
                    <tr
                      key={p.paket}
                      className={p.fark < 0 ? "row-neg" : undefined}
                    >
                      <td>{p.paket}</td>
                      <td className="num">
                        {int(p.adet_b)} → <b>{int(p.adet_a)}</b>
                      </td>
                      <td className="num">
                        {tlShort(p.ort_b)} → {tlShort(p.ort_a)}
                      </td>
                      <td className="num">{tl(p.ciro_b)}</td>
                      <td className="num">{tl(p.ciro_a)}</td>
                      <td className={`num ${tone(p.fark)}`}>
                        {arrow(p.fark)}
                        {signed(p.fark)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}

        <h3 className="bridge-sub">Görüşme kalitesi</h3>
        <div className="table-wrap">
          <table className="board">
            <thead>
              <tr>
                <th>Ölçü</th>
                <th className="num">{b.label}</th>
                <th className="num">{a.label}</th>
                <th className="num">Değişim</th>
              </tr>
            </thead>
            <tbody>
              {data.kalite.map((q) => (
                <tr
                  key={q.key}
                  className={
                    q.degisim !== null && q.degisim < 0 ? "row-neg" : undefined
                  }
                >
                  <td>{q.label}</td>
                  <td className="num">{showQuality(q.b, q.kind)}</td>
                  <td className="num">{showQuality(q.a, q.kind)}</td>
                  <td className={`num ${tone(q.degisim)}`}>
                    {arrow(q.degisim)}
                    {change(q.degisim, q.kind === "yuzde")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted cmp-note">
          Ciro formülünde yer almaz; adımların arkasındaki görüşme davranışını
          gösterir.
        </p>

        {data.movers.length ? (
          <>
            <h3 className="bridge-sub">En çok değişen temsilciler</h3>
            <div className="table-wrap">
              <table className="board">
                <thead>
                  <tr>
                    <th>Temsilci</th>
                    <th className="num">Ciro {b.label}</th>
                    <th className="num">Ciro {a.label}</th>
                    <th className="num">Fark</th>
                    <th className="num">Satış</th>
                    <th className="num">Satışa dönüş</th>
                    <th className="num">Ort. süre</th>
                  </tr>
                </thead>
                <tbody>
                  {data.movers.map((m) => (
                    <tr
                      key={m.rep_id}
                      className={m.fark < 0 ? "row-neg" : undefined}
                    >
                      <td>{m.name}</td>
                      <td className="num">{tl(m.ciro_b)}</td>
                      <td className="num">{tl(m.ciro_a)}</td>
                      <td className={`num ${tone(m.fark)}`}>
                        {arrow(m.fark)}
                        {signed(m.fark)}
                      </td>
                      <td className="num">
                        {int(m.satis_b)} → <b>{int(m.satis_a)}</b>
                      </td>
                      <td className="num">
                        {showFactor(m.donusum_b, "oran")} →{" "}
                        {showFactor(m.donusum_a, "oran")}
                      </td>
                      <td className="num">
                        {duration(m.sure_b)} → {duration(m.sure_a)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
