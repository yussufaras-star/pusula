import { usePolling } from "../api";
import { int, num, pct, tl, tlShort } from "../format";

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
}

interface BridgeSide {
  first: string;
  last: string;
  ciro: number;
  satis: number;
}

interface Bridge {
  a: BridgeSide;
  b: BridgeSide;
  fark: number;
  fark_orani: number | null;
  ayristi: boolean;
  factors: Factor[];
  movers: Mover[];
  paketler: Mix[];
  headline: string;
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

const shortDay = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  timeZone: "UTC",
});
const span = (s: BridgeSide) => {
  const f = shortDay.format(new Date(`${s.first}T12:00:00Z`));
  return s.first === s.last
    ? f
    : `${f} – ${shortDay.format(new Date(`${s.last}T12:00:00Z`))}`;
};

function showFactor(v: number | null, kind: Factor["kind"]): string {
  if (v === null) return "—";
  if (kind === "oran") return pct(v * 100);
  if (kind === "tl") return tl(v);
  if (kind === "gün") return int(v);
  return num(v);
}

const signed = (v: number) => `${v < 0 ? "−" : "+"}${tlShort(Math.abs(v))}`;
const tone = (v: number | null) =>
  v === null || Math.abs(v) < 1 ? "" : v < 0 ? "neg" : "pos";

/** Dönem karşılaştırmasında ciro farkının huni adımlarına ayrıştırılması. */
export function SalesBridge({ query }: { query: string }) {
  const { data, error } = usePolling<Bridge>(`/api/sales-bridge?${query}`, 0);

  if (error)
    return (
      <div className="banner error">
        Ciro ayrıştırması alınamadı: {error.message}
      </div>
    );
  if (!data) return <div className="skeleton" style={{ height: 220 }} />;

  const maxEffect = Math.max(
    1,
    ...data.factors.map((f) => Math.abs(f.etki ?? 0)),
  );
  return (
    <section className="card bridge">
      <div className="card-head">
        <h2>Ciro farkının kaynağı</h2>
        <span className="hint">
          A {span(data.a)} · B {span(data.b)} · tamamlanmış günler
        </span>
      </div>
      <div className="card-body">
        <div className="bridge-top">
          <div>
            <div className="bridge-k">Ciro</div>
            <div className="bridge-v">
              {tl(data.a.ciro)}{" "}
              <span className="muted">↔ {tl(data.b.ciro)}</span>
            </div>
            <div className="muted">
              {int(data.a.satis)} satış ↔ {int(data.b.satis)} satış
            </div>
          </div>
          <div className={`bridge-delta ${tone(data.fark)}`}>
            {signed(data.fark)}
            {data.fark_orani !== null ? (
              <span>
                {" "}
                ({data.fark_orani < 0 ? "−" : "+"}%
                {int(Math.abs(data.fark_orani * 100))})
              </span>
            ) : null}
          </div>
        </div>
        <p className="bridge-headline">{data.headline}</p>

        <div className="table-wrap">
          <table className="board bridge-table">
            <thead>
              <tr>
                <th>Adım</th>
                <th className="num">A</th>
                <th className="num">B</th>
                <th className="num">Değişim</th>
                <th className="effect">Ciroya etkisi</th>
              </tr>
            </thead>
            <tbody>
              {data.factors.map((f) => {
                const w =
                  f.etki === null ? 0 : (Math.abs(f.etki) / maxEffect) * 50;
                return (
                  <tr key={f.key}>
                    <td>{f.label}</td>
                    <td className="num">{showFactor(f.a, f.kind)}</td>
                    <td className="num">{showFactor(f.b, f.kind)}</td>
                    <td className={`num ${tone(f.degisim)}`}>
                      {f.degisim === null
                        ? "—"
                        : `${f.degisim < 0 ? "−" : "+"}%${num(Math.abs(f.degisim * 100))}`}
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
                    <th className="num">Adet A ↔ B</th>
                    <th className="num">Ort. tutar A ↔ B</th>
                    <th className="num">Ciro A</th>
                    <th className="num">Ciro B</th>
                    <th className="num">Fark</th>
                  </tr>
                </thead>
                <tbody>
                  {data.paketler.map((p) => (
                    <tr key={p.paket}>
                      <td>{p.paket}</td>
                      <td className="num">
                        {int(p.adet_a)} ↔ {int(p.adet_b)}
                      </td>
                      <td className="num">
                        {tlShort(p.ort_a)} ↔ {tlShort(p.ort_b)}
                      </td>
                      <td className="num">{tl(p.ciro_a)}</td>
                      <td className="num">{tl(p.ciro_b)}</td>
                      <td className={`num ${tone(p.fark)}`}>
                        {signed(p.fark)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : null}

        {data.movers.length ? (
          <>
            <h3 className="bridge-sub">En çok değişen temsilciler</h3>
            <div className="table-wrap">
              <table className="board">
                <thead>
                  <tr>
                    <th>Temsilci</th>
                    <th className="num">A ciro</th>
                    <th className="num">B ciro</th>
                    <th className="num">Fark</th>
                    <th className="num">Satış A ↔ B</th>
                    <th className="num">Dönüş A ↔ B</th>
                  </tr>
                </thead>
                <tbody>
                  {data.movers.map((m) => (
                    <tr key={m.rep_id}>
                      <td>{m.name}</td>
                      <td className="num">{tl(m.ciro_a)}</td>
                      <td className="num">{tl(m.ciro_b)}</td>
                      <td className={`num ${tone(m.fark)}`}>
                        {signed(m.fark)}
                      </td>
                      <td className="num">
                        {int(m.satis_a)} ↔ {int(m.satis_b)}
                      </td>
                      <td className="num">
                        {showFactor(m.donusum_a, "oran")} ↔{" "}
                        {showFactor(m.donusum_b, "oran")}
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
