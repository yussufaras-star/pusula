import { useState } from "react";

import { usePolling } from "../api";
import { int, num, pct } from "../format";
import { Change } from "./CiroTrend";
import type { LeadChannelDim, LeadChannelRow, LeadChannelsPayload } from "../types";

const DIMS: { key: LeadChannelDim; label: string; col: string }[] = [
  { key: "kanal", label: "Kanal", col: "Kanal (UTM kaynağı)" },
  { key: "kampanya", label: "Kampanya", col: "Kampanya" },
  { key: "form", label: "Form türü", col: "Form türü (Zoho lead kaynağı)" },
];
/** Tabloda gösterilen son hafta sütunu. */
const TABLE_WEEKS = 6;
const TAIL = new Set(["Diğer", "UTM yok", "Kampanya yok", "Belirtilmemiş"]);

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const d = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));
const short = (iso: string) => d(iso).replace(".", "");

function movers(rows: LeadChannelRow[]): string | null {
  const named = rows.filter((r) => !TAIL.has(r.ad) && r.degisim !== null);
  if (!named.length) return null;
  const up = [...named].sort((a, b) => (b.degisim ?? 0) - (a.degisim ?? 0))[0];
  const down = [...named].sort((a, b) => (a.degisim ?? 0) - (b.degisim ?? 0))[0];
  const part = (r: LeadChannelRow) => `${r.ad} ${r.onceki} → ${r.son} (${(r.degisim ?? 0) > 0 ? "+" : "−"}%${num(Math.abs(r.degisim ?? 0))})`;
  const out = [];
  if ((up.degisim ?? 0) > 0) out.push(`En çok artan: ${part(up)}`);
  if ((down.degisim ?? 0) < 0) out.push(`en çok düşen: ${part(down)}`);
  return out.length ? `${out.join("; ")}.` : null;
}

/** Gelen lead'in kanal / kampanya / form kırılımı: pazarlama için adet, değişim, pay ve kalite. */
export function LeadChannels({ weeks }: { weeks: number }) {
  const [dim, setDim] = useState<LeadChannelDim>("kanal");
  const { data, error } = usePolling<LeadChannelsPayload>(`/api/lead-channels?dim=${dim}&weeks=${weeks}`, 0);
  const meta = DIMS.find((x) => x.key === dim) ?? DIMS[0];

  const chips = (
    <div className="chips trend-chips" role="group" aria-label="Kırılım">
      {DIMS.map((x) => (
        <button key={x.key} className={`chip${dim === x.key ? " on" : ""}`} onClick={() => setDim(x.key)} aria-pressed={dim === x.key}>
          {x.label}
        </button>
      ))}
    </div>
  );
  const head = (
    <div className="lf-chart-title lf-section">Kanal kırılımı · pazarlama</div>
  );
  if (error) return <>{head}{chips}<div className="banner error">Kanal kırılımı alınamadı: {error.message}</div></>;
  if (!data || data.dim !== dim || (data.haftalar && data.haftalar.length !== weeks + 1)) return <>{head}{chips}<div className="skeleton" /></>;
  if (!data.available || !data.satirlar || !data.toplam || !data.haftalar) {
    return <>{head}{chips}<div className="empty">Lead kaynak bilgisi ilk saatlik güncellemeyle gelecek.</div></>;
  }

  const cols = data.haftalar.slice(-(TABLE_WEEKS + 1));
  const offset = data.haftalar.length - cols.length;
  const rows = data.satirlar;
  const total = data.toplam;
  const summary = movers(rows);
  const blank = rows.find((r) => r.ad === "Belirtilmemiş");

  const line = (r: LeadChannelRow, isTotal = false) => (
    <tr key={r.ad} className={TAIL.has(r.ad) ? "muted" : ""}>
      <td className="name">{r.ad}</td>
      {cols.map((w, i) => (
        <td key={w.bas} className={`num${w.suruyor ? " muted" : ""}`}>
          {int(r.seri[offset + i])}
        </td>
      ))}
      <td className="num"><Change v={r.degisim} /></td>
      <td className="num">{isTotal ? "%100" : pct(r.pay)}</td>
      <td className="num">{num(r.ort4)}</td>
      <td className="num"><Change v={r.degisim_ort4} /></td>
      <td className="num lf-q">{pct(r.kalite.ulasildi)}</td>
      <td className="num">{pct(r.kalite.randevu)}</td>
      <td className="num">{pct(r.kalite.nitelikli_degil)}</td>
      <td className="num">{pct(r.kalite.hatali)}</td>
      <td className="num">
        {int(r.kalite.satis)}
        {r.kalite.lead ? <span className="muted"> · {pct(r.kalite.satis_orani)}</span> : null}
      </td>
    </tr>
  );

  return (
    <>
      {head}
      {chips}
      {summary ? <p className="lf-summary">{summary}</p> : null}
      {dim !== "form" && data.utm_payi !== null && data.utm_payi !== undefined ? (
        <p className="muted lf-note">
          Lead'lerin %{num(data.utm_payi)}'sinde {dim === "kanal" ? "UTM kaynağı" : "kampanya"} var; geri kalanın{" "}
          {dim === "kanal" ? "kanalı" : "kampanyası"} bilinmiyor ("{dim === "kanal" ? "UTM yok" : "Kampanya yok"}" satırı).
        </p>
      ) : null}
      {dim === "form" && blank && (blank.pay ?? 0) >= 50 ? (
        <div className="banner">
          Geçen hafta lead'lerin %{num(blank.pay)}'sinde Zoho "Lead kaynağı" alanı boş; form türü ayrımı bu haftalarda güvenilir değil.
        </div>
      ) : null}
      <div className="table-wrap">
        <table className="board lf-channels">
          <thead>
            <tr>
              <th rowSpan={2}>{meta.col}</th>
              <th colSpan={cols.length} className="lf-group">Gelen lead · hafta</th>
              <th colSpan={4} className="lf-group">Değişim</th>
              <th colSpan={5} className="lf-group" title={`${d(data.kalite_bas ?? "")} – ${d(data.kalite_bit ?? "")} arasında gelen lead'ler, bugünkü statüleriyle`}>
                Kalite · son 4 hafta
              </th>
            </tr>
            <tr>
              {cols.map((w) => (
                <th key={w.bas} className={`num${w.suruyor ? " muted" : ""}`} title={`${d(w.bas)} – ${d(w.bit)}`}>
                  {w.suruyor ? "Bu hafta" : short(w.bas)}
                </th>
              ))}
              <th className="num" title="Geçen hafta / önceki hafta">Haftalık</th>
              <th className="num" title="Geçen haftaki toplam içindeki pay">Pay</th>
              <th className="num" title="Son 4 tamamlanmış haftanın haftalık ortalaması">4 hf ort.</th>
              <th className="num" title="Son 4 hafta / önceki 4 hafta">4 hf</th>
              <th className="num" title="Ulaşılan / işlenen lead; hatalı ve yabancı numara ulaşılmış sayılmaz">Ulaşıldı</th>
              <th className="num" title="Statüsü Randevu ya da Satış Fırsatı">Randevu</th>
              <th className="num" title="Nitelikli Değil-İlgisiz ya da Aranmamalı">Nitelikli değil</th>
              <th className="num" title="Numara hatalı ya da yabancı numara">Hatalı no</th>
              <th className="num" title="Lead'in kişi kaydına bağlı, lead'den sonra kapanan kazanılmış satış">Satış</th>
            </tr>
          </thead>
          <tbody>{rows.map((r) => line(r))}</tbody>
          <tfoot>{line(total, true)}</tfoot>
        </table>
      </div>
      <p className="muted fn-note">
        Lead: Zoho'da oluşturulduğu gün; aynı kişinin tekrar başvurusu da sayılır (üstteki tekil sayımdan ~%1 fazla). Haftalık %
        ve 4 haftalık % önceki dönemde 5'ten az lead varsa hesaplanmaz; 8 haftada 5'ten az lead gelen kanallar "Diğer"de. Kalite
        son 4 tamamlanmış haftada gelen lead'lerin bugünkü statüsünden; yeni lead'lerin statüsü ve satışı zamanla oturur, bu yüzden
        oranları kanallar arasında kıyaslamak için kullan.
      </p>
    </>
  );
}
