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
  tespitler: { grup: Group; ton: "neg" | "pos"; metin: string }[];
  sonar: Sonar | null;
  oneriler: string[];
  factors: Factor[];
  movers: Mover[];
  paketler: Mix[];
  kalite: Quality[];
  pazarlama: Marketing | null;
}

type MarketingGroup = "fiyat" | "kanal" | "veri";

const MARKETING_GROUPS: [MarketingGroup, string][] = [
  ["fiyat", "Yeni yıl fiyat artışı"],
  ["kanal", "Lead kanalları"],
  ["veri", "Veri ve ölçüm"],
];

interface Marketing {
  lead_a: number;
  lead_b: number;
  lead_degisim: number | null;
  kanallar: {
    kanal: string;
    ad: string;
    lead_a: number;
    lead_b: number;
    satis: number;
    degisim: number | null;
    oran: number | null;
  }[];
  tespitler: { grup: MarketingGroup; ton: "neg" | "pos"; metin: string }[];
  oneriler: { grup: MarketingGroup; metin: string }[];
}

type Group = "ciro" | "lead" | "sonar" | "kisi";

const GROUPS: [Group, string][] = [
  ["ciro", "Ciro adımları"],
  ["lead", "Lead ve görüşme"],
  ["sonar", "SONAR-41"],
  ["kisi", "Temsilciler"],
];

interface Sonar {
  ort_a: number | null;
  ort_b: number | null;
  n_a: number;
  n_b: number;
  gerileyen: {
    kod: string;
    ad: string;
    a: number;
    b: number;
    n_a: number;
    n_b: number;
  }[];
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

const scoreDiff = (a: number | null, b: number | null) =>
  a === null || b === null ? null : a - b;
const score = (v: number | null) =>
  v === null ? "—" : v.toFixed(2).replace(".", ",");

/** SONAR puan farkı: ▼ −0,35 puan. */
function points(v: number | null): string {
  if (v === null) return "—";
  return `${arrow(v)}${v < 0 ? "−" : "+"}${score(Math.abs(v))} puan`;
}

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
    <>
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
              {GROUPS.map(([grup, title]) => {
                const items = data.tespitler.filter((t) => t.grup === grup);
                if (!items.length) return null;
                return (
                  <div key={grup} className="bridge-group">
                    <h4>{title}</h4>
                    <ul className="bridge-findings">
                      {items.map((t, i) => (
                        <li key={i} className={t.ton}>
                          <span className="bridge-mark" aria-hidden>
                            {t.ton === "neg" ? "▼" : "▲"}
                          </span>
                          {t.metin}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </div>
            {data.oneriler.length ? (
              <div className="bridge-advice">
                <h3 className="bridge-sub">
                  Ekip için: satışları artırmak için ne yapmalı?
                </h3>
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
            Ciro = iş günü × günlük aranan lead × ulaşma × satışa dönüş ×
            ortalama satış. Etkiler toplanınca farkı verir.
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

          <h3 className="bridge-sub">Lead ve görüşme</h3>
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
                      q.degisim !== null && q.degisim < 0
                        ? "row-neg"
                        : undefined
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

          {data.sonar ? (
            <>
              <h3 className="bridge-sub">SONAR-41</h3>
              <div className="table-wrap">
                <table className="board">
                  <thead>
                    <tr>
                      <th>Davranış</th>
                      <th className="num">{b.label}</th>
                      <th className="num">{a.label}</th>
                      <th className="num">Değişim</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      className={
                        tone(scoreDiff(data.sonar.ort_a, data.sonar.ort_b)) ===
                        "neg"
                          ? "row-neg"
                          : undefined
                      }
                    >
                      <td>
                        <b>Genel ortalama</b>{" "}
                        <span className="muted">
                          ({int(data.sonar.n_b)} → {int(data.sonar.n_a)}{" "}
                          görüşme)
                        </span>
                      </td>
                      <td className="num">{score(data.sonar.ort_b)}</td>
                      <td className="num">{score(data.sonar.ort_a)}</td>
                      <td
                        className={`num ${tone(scoreDiff(data.sonar.ort_a, data.sonar.ort_b))}`}
                      >
                        {points(scoreDiff(data.sonar.ort_a, data.sonar.ort_b))}
                      </td>
                    </tr>
                    {data.sonar.gerileyen.map((g) => (
                      <tr key={g.kod} className="row-neg">
                        <td>
                          {g.ad} <span className="muted">({g.kod})</span>
                        </td>
                        <td className="num">{score(g.b)}</td>
                        <td className="num">{score(g.a)}</td>
                        <td className="num neg">{points(g.a - g.b)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted cmp-note">
                5 üzerinden. Gerileme: iki dönemde de en az 10 puanlanmış an ve
                en az 0,3 puan düşüş.
                {data.sonar.gerileyen.length
                  ? ""
                  : " Bu dönemde belirgin gerileyen davranış yok."}
              </p>
            </>
          ) : null}

          {data.movers.length ? (
            <>
              <h3 className="bridge-sub">Temsilci bazında</h3>
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
      {data.pazarlama ? (
        <MarketingCard m={data.pazarlama} a={a} b={b} isAdmin={isAdmin} />
      ) : null}
    </>
  );
}

/** Pazarlama ekibine: fiyat artışı, lead kanalları, veri; aynı iki dönem. */
function MarketingCard({
  m,
  a,
  b,
  isAdmin,
}: {
  m: Marketing;
  a: BridgeSide;
  b: BridgeSide;
  isAdmin: boolean;
}) {
  return (
    <section className="card bridge" data-share>
      <div className="card-head">
        <div>
          <h2>Pazarlama ekibi için öneriler</h2>
          <span className="hint">
            {b.label} → {a.label} · gelen lead {int(m.lead_b)} → {int(m.lead_a)}
            {m.lead_degisim === null ? "" : ` (${change(m.lead_degisim)})`}
          </span>
        </div>
        {isAdmin ? (
          <ShareButton
            kart="pazarlama"
            baslik={`Pazarlama önerileri · ${b.label} → ${a.label}`}
          />
        ) : null}
      </div>
      <div className="card-body">
        <div className="bridge-cols">
          <div>
            <h3 className="bridge-sub">Tespitler</h3>
            {MARKETING_GROUPS.map(([grup, title]) => {
              const items = m.tespitler.filter((t) => t.grup === grup);
              if (!items.length) return null;
              return (
                <div key={grup} className="bridge-group">
                  <h4>{title}</h4>
                  <ul className="bridge-findings">
                    {items.map((t, i) => (
                      <li key={i} className={t.ton}>
                        <span className="bridge-mark" aria-hidden>
                          {t.ton === "neg" ? "▼" : "▲"}
                        </span>
                        {t.metin}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
          {m.oneriler.length ? (
            <div className="bridge-advice">
              <h3 className="bridge-sub">
                Pazarlama: satışları artırmak için ne yapmalı?
              </h3>
              {MARKETING_GROUPS.map(([grup, title]) => {
                const items = m.oneriler.filter((o) => o.grup === grup);
                if (!items.length) return null;
                return (
                  <div key={grup} className="bridge-group">
                    <h4>{title}</h4>
                    <ol>
                      {items.map((o, i) => (
                        <li key={i}>{o.metin}</li>
                      ))}
                    </ol>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>

        <h3 className="bridge-sub">Kanal bazında gelen lead</h3>
        <div className="table-wrap">
          <table className="board">
            <thead>
              <tr>
                <th>Kanal</th>
                <th className="num">{b.label}</th>
                <th className="num">{a.label}</th>
                <th className="num">Değişim</th>
                <th className="num">Satış (iki dönem)</th>
                <th className="num">Lead'den satış</th>
              </tr>
            </thead>
            <tbody>
              {m.kanallar.map((k) => (
                <tr
                  key={k.kanal}
                  className={
                    k.degisim !== null && k.degisim < 0 ? "row-neg" : undefined
                  }
                >
                  <td>{k.ad}</td>
                  <td className="num">{int(k.lead_b)}</td>
                  <td className="num">{int(k.lead_a)}</td>
                  <td className={`num ${tone(k.degisim)}`}>
                    {arrow(k.degisim)}
                    {change(k.degisim)}
                  </td>
                  <td className="num">{int(k.satis)}</td>
                  <td className="num">{showFactor(k.oran, "oran")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted cmp-note">
          Lead, Zoho'da oluşturulduğu gün sayılır; kanal UTM kaynağıdır. Satış:
          lead'den sonra kapanan kazanılmış satış. Yeni lead'lerin satışı henüz
          oturmadığından lead'den satış oranı iki dönem birlikte hesaplanır.
        </p>
      </div>
    </section>
  );
}
