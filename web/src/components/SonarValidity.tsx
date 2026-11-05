import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { ShareButton } from "./ShareButton";
import { ToneBadge } from "./StatTile";

// SONAR-41 tutarlılık analizi: satışla biten görüşmeler daha yüksek puan alıyor mu? Ekip geneli, isim yok.

interface Group {
  n: number;
  ort: number | null;
  puanlar: Record<string, number | null>;
}

interface Band {
  etiket: string;
  n: number;
  satis: number;
  oran: number | null;
}

interface Validity {
  since: string;
  pencere_gun: number;
  score_labels: Record<string, string>;
  n: number;
  satisli: Group;
  satissiz: Group;
  fark: number | null;
  boyut_fark: Record<string, number | null>;
  en_ayirici: string[];
  en_az_ayirici: string | null;
  dilimler: Band[];
  kontrol: { ilgili_satisli: Group; ilgili_satissiz: Group; satis_oncesi: Group };
  guven: "ön sonuç" | "orta" | "yüksek";
}

interface Payload {
  kocluk: Validity;
  havuz: Validity | null;
  havuz_durum: { toplam: number; analiz: number; sirada: number; hata: number };
}

type Scope = "havuz" | "kocluk";

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "long", timeZone: "UTC" });
const f2 = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : "−"}${f2(Math.abs(v))}`);

/** Puan dilimine göre satış oranı (dikey çubuk, üstünde %). */
function BandChart({ bands }: { bands: Band[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 360;
  const H = 220;
  const P = { top: 24, right: 8, bottom: 44, left: 8 };
  const max = Math.max(10, ...bands.map((b) => b.oran ?? 0));
  const iw = W - P.left - P.right;
  const ih = H - P.top - P.bottom;
  const slot = iw / bands.length;
  const bw = Math.min(56, slot * 0.6);
  const y = (v: number) => P.top + ih - (v / max) * ih;
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Görüşme puanı dilimine göre satış oranı" onMouseLeave={() => setHover(null)}>
        <line x1={P.left} x2={W - P.right} y1={y(0)} y2={y(0)} className="gridline" />
        {bands.map((b, i) => {
          const v = b.oran ?? 0;
          const x = P.left + slot * i + (slot - bw) / 2;
          return (
            <g key={b.etiket} onMouseEnter={() => setHover(i)}>
              <rect x={P.left + slot * i} y={P.top} width={slot} height={ih} fill="transparent" />
              <rect x={x} y={y(v)} width={bw} height={Math.max(y(0) - y(v), 0)} rx={3} className={`ciro-bar${hover === i ? " on" : ""}`} />
              <text x={x + bw / 2} y={y(v) - 6} textAnchor="middle" className="sv-val">
                {b.oran === null ? "—" : `%${num(b.oran)}`}
              </text>
              <text x={x + bw / 2} y={H - P.bottom + 16} textAnchor="middle" className="tick sv-band">
                {b.etiket}
              </text>
              <text x={x + bw / 2} y={H - P.bottom + 31} textAnchor="middle" className="tick">
                {int(b.n)} görüşme
              </text>
            </g>
          );
        })}
      </svg>
      {hover !== null ? (
        <div className="tooltip" style={{ left: `${((P.left + slot * hover + slot / 2) / W) * 100}%`, top: `${(y(bands[hover].oran ?? 0) / H) * 100}%` }}>
          <b>Puan {bands[hover].etiket}</b>
          <div>
            {int(bands[hover].n)} görüşmeden {int(bands[hover].satis)} satış
          </div>
        </div>
      ) : null}
    </div>
  );
}

/** Başlık başına satışsız (gri) → satışlı (mavi) ortalama; fark büyükse başlık satışı ayırıyor. */
function Dumbbell({ data }: { data: Validity }) {
  const dims = Object.keys(data.score_labels).sort((a, b) => (data.boyut_fark[b] ?? -9) - (data.boyut_fark[a] ?? -9));
  const W = 420;
  const row = 34;
  const P = { top: 22, right: 54, bottom: 26, left: 150 };
  const H = P.top + row * dims.length + P.bottom;
  const lo = 1;
  const hi = 4;
  const x = (v: number) => P.left + ((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * (W - P.left - P.right);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Başlık başına satışlı ve satışsız görüşme puanı">
        {[1, 2, 3, 4].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={P.top - 6} y2={H - P.bottom} className="gridline" />
            <text x={x(t)} y={H - 8} textAnchor="middle" className="tick">
              {t}
            </text>
          </g>
        ))}
        {dims.map((k, i) => {
          const cy = P.top + row * i + row / 2;
          const a = data.satissiz.puanlar[k];
          const b = data.satisli.puanlar[k];
          const top = data.en_ayirici.includes(k);
          return (
            <g key={k}>
              <text x={P.left - 10} y={cy + 4} textAnchor="end" className={`tick sv-dim${top ? " top" : ""}`}>
                {k} {data.score_labels[k]}
              </text>
              {a !== null && b !== null ? (
                <>
                  <line x1={x(a)} x2={x(b)} y1={cy} y2={cy} className="sv-bar" />
                  <circle cx={x(a)} cy={cy} r={6} className="sv-dot no">
                    <title>Satışsız: {f2(a)}</title>
                  </circle>
                  <circle cx={x(b)} cy={cy} r={6} className="sv-dot yes">
                    <title>Satışlı: {f2(b)}</title>
                  </circle>
                  <text x={W - P.right + 8} y={cy + 4} className={`sv-gap${top ? " top" : ""}`}>
                    {signed(data.boyut_fark[k])}
                  </text>
                </>
              ) : null}
            </g>
          );
        })}
      </svg>
      <div className="sv-legend">
        <span>
          <i className="sv-key no" /> satış olmayan görüşmeler
        </span>
        <span>
          <i className="sv-key yes" /> satışla biten görüşmeler
        </span>
      </div>
    </div>
  );
}

const CONF_TONE = { "ön sonuç": "warning", orta: "neutral", yüksek: "good" } as const;

export function SonarValidity({ isAdmin }: { isAdmin: boolean }) {
  const { data: payload, error } = usePolling<Payload>("/api/conversations/validity", 0);
  const [picked, setPicked] = useState<Scope | null>(null);
  if (error) return null;
  if (!payload) return <div className="skeleton" />;
  const pool = payload.havuz;
  const poolReady = !!pool && pool.satisli.n >= 30 && pool.satissiz.n >= 30;
  const scope: Scope = picked ?? (poolReady ? "havuz" : "kocluk");
  const data = scope === "havuz" && pool ? pool : payload.kocluk;
  const st = payload.havuz_durum;
  if (!data.satisli.n || !data.satissiz.n) return null;

  const bands = data.dilimler.filter((b) => b.n > 0);
  const low = bands[0];
  const high = bands[bands.length - 1];
  const k = data.kontrol;
  const lead = data.en_ayirici.map((x) => `${x} ${data.score_labels[x]}`).join(" ve ");

  return (
    <section className="card" data-share="sonar-tutarlilik">
      <div className="card-head">
        <h2>SONAR-41 tutarlılık analizi</h2>
        <span className="hint">puan satışı gösteriyor mu · {dayFmt.format(new Date(`${data.since}T00:00:00Z`))}'dan beri</span>
        {pool ? (
          <div className="chips" role="group" aria-label="Kapsam" data-share-skip>
            {(["havuz", "kocluk"] as const).map((k) => (
              <button key={k} className={`chip${scope === k ? " on" : ""}`} onClick={() => setPicked(k)} aria-pressed={scope === k}>
                {k === "havuz" ? "2026 havuzu" : "Koçluk dönemi"}
              </button>
            ))}
          </div>
        ) : null}
        {isAdmin ? <ShareButton kart="sonar-tutarlilik" baslik={`SONAR-41 tutarlılık analizi · ${scope === "havuz" ? "2026 havuzu" : "koçluk dönemi"}`} /> : null}
      </div>
      <div className="card-body">
        {st.toplam && st.sirada ? (
          <div className="banner" data-share-skip>
            2026 havuzu işleniyor: {int(st.toplam)} görüşmeden {int(st.analiz)} analiz edildi, {int(st.sirada)} sırada
            {st.hata ? `, ${int(st.hata)} kayıt alınamadı` : ""}. Gece işinde tamamlanır.
          </div>
        ) : null}
        <p className="sv-lead">
          Satışla biten görüşmeler ortalama <b>{f2(data.satisli.ort)}</b>, satış olmayanlar <b>{f2(data.satissiz.ort)}</b>{" "}
          puan aldı.
          {low && high && low !== high ? (
            <>
              {" "}Puanı {high.etiket.toLowerCase()} olan görüşmelerin <b>%{num(high.oran)}</b>'i satışa döndü;{" "}
              {low.etiket.toLowerCase()} kalanların yalnız <b>%{num(low.oran)}</b>'i.
            </>
          ) : null}
        </p>
        <p className="sv-msg">
          Ekibe mesaj: <b>SONAR puanı süs değil.</b> En çok fark <b>{lead}</b> başlıklarında; bunlardaki puanını yükselttiğinde
          satışın artıyor.
        </p>
        <div className="sv-conf">
          <ToneBadge tone={CONF_TONE[data.guven]}>{data.guven}</ToneBadge>
          <span className="muted">
            {int(data.satisli.n)} satışlı, {int(data.satissiz.n)} satışsız görüşme · 30 satışlı görüşmede orta, 60'ta yüksek güven
          </span>
        </div>

        <div className="lf-grid">
          <div>
            <div className="lf-chart-title">Puan yükseldikçe satış oranı</div>
            <BandChart bands={data.dilimler} />
          </div>
          <div>
            <div className="lf-chart-title">Hangi başlık satışı ayırıyor</div>
            <Dumbbell data={data} />
          </div>
        </div>

        <div className="sv-checks">
          <div className="sv-check">
            <div className="sv-check-label">Müşteri zaten hazır mıydı?</div>
            <div>
              İlgisi yüksek (4-5) müşterilerde de satışlı görüşme <b>{f2(k.ilgili_satisli.ort)}</b>, satışsız{" "}
              <b>{f2(k.ilgili_satissiz.ort)}</b>.
            </div>
            <div className="muted">
              {int(k.ilgili_satisli.n)} / {int(k.ilgili_satissiz.n)} görüşme
            </div>
          </div>
          <div className="sv-check">
            <div className="sv-check-label">Puan satış olduğu için mi yüksek?</div>
            <div>
              Satıştan günler önceki görüşmeler (metninde satış yok) de <b>{f2(k.satis_oncesi.ort)}</b>; satışsızlar{" "}
              <b>{f2(data.satissiz.ort)}</b>.
            </div>
            <div className="muted">{int(k.satis_oncesi.n)} görüşme</div>
          </div>
          {data.en_az_ayirici ? (
            <div className="sv-check">
              <div className="sv-check-label">Satışı ayırmayan başlık</div>
              <div>
                <b>
                  {data.en_az_ayirici} {data.score_labels[data.en_az_ayirici]}
                </b>{" "}
                ({signed(data.boyut_fark[data.en_az_ayirici])}): satış olsa da olmasa da benzer puan.
              </div>
              <div className="muted">ya satışı belirlemiyor ya puanlaması gözden geçirilmeli</div>
            </div>
          ) : null}
        </div>

        <p className="muted fn-note">
          {scope === "havuz"
            ? "2026 havuzu: satışa dönmüş görüşmelerin hepsi ve satışa dönmemiş görüşmelerden aylara orantılı rastgele örnek; satış oranı örnek ağırlığıyla hesaplanır. Verimor kayıtları ~3 ay sakladığı için havuz Ağustos'tan başlar. "
            : "Koçluk dönemi: haftalık analiz edilen görüşmeler (temsilci başına haftada ~15, kota seçimi). "}
          Görüşmeden sonraki {data.pencere_gun} gün içinde (aynı gün dahil) aynı müşteriye satış kapandıysa görüşme "satışla
          biten" sayılır; müşteri kişi kaydı üzerinden satışa bağlanır. Daha önce satın almış müşterilerin görüşmeleri
          (destek, yenileme) analize girmez. Görüşme puanı beş başlığın ortalamasıdır. Bu analiz ilişkiyi gösterir; örneklem
          büyüdükçe sonuç kesinleşir.
        </p>
      </div>
    </section>
  );
}
