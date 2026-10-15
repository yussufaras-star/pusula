import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { StatTile } from "./StatTile";
import type { PaketGroup, PaketPayload } from "../types";

const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric", timeZone: "UTC" });
const OWNERS = ["Nihat Kemer", "Burcu Özkan"];
const TUR: Record<PaketGroup["tur"], string> = { aktif: "aktif", pasif: "churn", bos: "girilmemiş" };
const EXIT_COLS: [string, string][] = [
  ["free", "Free"],
  ["freemium", "Freemium"],
  ["inactive", "Inactive"],
  ["bosaltilmis", "Boşaltılmış"],
];

const pct = (part: number, whole: number) => (whole ? `%${num((100 * part) / whole)}` : "—");
const cell = (v: number) => (v ? int(v) : <span className="muted">—</span>);

/** Paket bazlı müşteri dağılımı: aktif / churn / girilmemiş gruplar ve aylık çıkışlar. */
export function PaketBoard() {
  const [owner, setOwner] = useState("");
  const { data, error } = usePolling<PaketPayload>(`/api/paket${owner ? `?owner=${encodeURIComponent(owner)}` : ""}`, 0);
  if (error) return <div className="banner error">Paket verisi alınamadı: {error.message}</div>;
  if (!data) return <div className="skeleton" />;
  if (!data.available) return <div className="empty">Müşteri profilleri ilk saatlik güncellemeyle gelecek.</div>;

  const total = data.toplam ?? 0;
  const groups = data.gruplar ?? [];
  const exits = data.cikislar ?? [];
  const exitCols = EXIT_COLS.filter(([k]) => exits.some((m) => Number(m[k]) > 0));
  const last30 = groups.reduce((a, g) => a + g.cikis_30g, 0);
  const thisYear = groups.reduce((a, g) => a + g.cikis_bu_yil, 0);

  return (
    <>
      <div className="ss-controls">
        <div className="chips" role="group" aria-label="Müşteri sahibi">
          {["", ...OWNERS].map((o) => (
            <button key={o || "all"} className={`chip${owner === o ? " on" : ""}`} onClick={() => setOwner(o)} aria-pressed={owner === o}>
              {o || "Tüm müşteriler"}
            </button>
          ))}
        </div>
      </div>

      <div className="tiles">
        <StatTile label="Aktif (Premium / Plus / Elite)" value={int(data.aktif)} expected={`${pct(data.aktif ?? 0, total)} · ${int(total)} müşteri`} />
        <StatTile
          label="Churn paketi"
          value={int(data.pasif)}
          expected={`${pct(data.pasif ?? 0, total)} · free, Freemium, Inactive, boşaltılmış`}
          tone={data.pasif ? "warning" : "neutral"}
        />
        <StatTile label="Son 30 günde paketten çıkan" value={int(last30)} expected={`${int(thisYear)} bu yıl`} tone={last30 ? "warning" : "neutral"} />
        <StatTile label="Paket girilmemiş" value={int(data.bos)} expected={`${pct(data.bos ?? 0, total)} · Zoho'da Paket boş`} />
      </div>

      <div className="table-wrap">
        <table className="board">
          <thead>
            <tr>
              <th>Paket</th>
              <th className="num">Müşteri</th>
              <th className="num">Pay</th>
              {OWNERS.map((o) => (
                <th key={o} className="num">
                  {o.split(" ")[0]}
                </th>
              ))}
              <th className="num" title="Diğer veya boş sahip">Diğer</th>
              <th className="num" title="Son 1 yıllık satışı > 0 (müşterinin kendi mağazası)">Satıyor</th>
              <th className="num" title="Zoho abonelik bitişi geçmiş">Bitişi geçmiş</th>
              <th className="num" title={`Abonelik bitişi ${data.yakin_gun ?? 60} gün içinde`}>{data.yakin_gun ?? 60} günde bitecek</th>
              <th className="num" title="CRM'de kazanılmış abonelik satışı olan">CRM aboneliği</th>
              <th className="num" title="CRM'de kazanılmış Re-Co satışı olan">Re-Co</th>
              <th className="num" title="Aktif paketten bu yıl çıkan">Bu yıl çıkan</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.key}>
                <td className="name">
                  {g.ad}
                  <span className="sub"> · {TUR[g.tur]}</span>
                </td>
                <td className="num">
                  <b>{int(g.musteri)}</b>
                </td>
                <td className="num muted">{g.pay === null ? "—" : `%${num(g.pay)}`}</td>
                {OWNERS.map((o) => (
                  <td key={o} className="num">
                    {cell(g.sahip[o] ?? 0)}
                  </td>
                ))}
                <td className="num">{cell(g.sahip["Diğer"] ?? 0)}</td>
                <td className="num">
                  {cell(g.satiyor)}
                  {g.satiyor ? <span className="sub"> {pct(g.satiyor, g.musteri)}</span> : null}
                </td>
                <td className="num">{g.bitis_gecmis ? <b className="warn-text">{int(g.bitis_gecmis)}</b> : cell(0)}</td>
                <td className="num">{cell(g.bitis_yakin)}</td>
                <td className="num">{cell(g.abonelik_satisi)}</td>
                <td className="num">{cell(g.reco)}</td>
                <td className="num">{g.tur === "pasif" ? cell(g.cikis_bu_yil) : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {exits.length ? (
        <>
          <h3 className="fn-subhead">Aktif paketten çıkışlar · aylık</h3>
          <div className="table-wrap">
            <table className="board">
              <thead>
                <tr>
                  <th>Ay</th>
                  {exitCols.map(([, label]) => (
                    <th key={label} className="num">
                      {label}
                    </th>
                  ))}
                  <th className="num">Toplam</th>
                </tr>
              </thead>
              <tbody>
                {exits.map((m) => (
                  <tr key={m.ay}>
                    <td className="name">{monthFmt.format(new Date(`${m.ay}T00:00:00Z`))}</td>
                    {exitCols.map(([k]) => (
                      <td key={k} className="num">
                        {cell(Number(m[k]))}
                      </td>
                    ))}
                    <td className="num">
                      <b>{int(m.toplam)}</b>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}

      <p className="muted fn-note">
        Kaynak: Zoho Contacts "Paket" alanı (saatlik). Premium, Plus ve Elite aktif; free, Freemium, Inactive ve Premium'dan
        boşaltılmış paket churn sayılır. Paket hiç girilmemişse (çoğu yeni satış) ayrı gösterilir. Çıkış ayı: Zoho
        geçmişinde paketin aktif paketten çıktığı gün. Abonelik bitişi Zoho müşteri kaydından; free müşterilerde güvenilir
        değildir.
      </p>
    </>
  );
}
