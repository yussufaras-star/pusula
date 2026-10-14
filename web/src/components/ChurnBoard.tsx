import { useState } from "react";

import { usePolling } from "../api";
import { int, num } from "../format";
import { StatTile } from "./StatTile";
import type { ChurnItem, ChurnPayload, ChurnPeriod } from "../types";

const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "long", year: "numeric", timeZone: "UTC" });
const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const day = (iso: string) => dayFmt.format(new Date(`${iso}T00:00:00Z`));
const OWNERS = ["Nihat Kemer", "Burcu Özkan"];

type View = "yaklasan" | "kaybedilen" | "aylik";

const rate = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `%${num(v)}`);

function periodNote(p: ChurnPeriod | undefined): string {
  if (!p) return "";
  return `${int(p.kaybedildi)} kaybedildi · ${int(p.yeniledi)} yeniledi · ${int(p.geri_kazanildi)} geri kazanıldı`;
}

function dueTone(days: number): string {
  if (days <= 7) return "critical";
  if (days <= 30) return "warning";
  return "neutral";
}

/** Müşteri iş listesi: yaklaşan vadeler (yenileme araması) veya kaybedilenler (geri kazanma). */
function ItemTable({ items, kind }: { items: ChurnItem[]; kind: "yaklasan" | "kaybedilen" }) {
  if (!items.length) {
    return (
      <div className="empty">
        {kind === "yaklasan" ? "Önümüzdeki günlerde vadesi dolan müşteri yok." : "Bu dönemde kaybedilen müşteri yok."}
      </div>
    );
  }
  return (
    <div className="table-wrap">
      <table className="board churn-list">
        <thead>
          <tr>
            <th>Müşteri</th>
            <th>Sahip</th>
            <th>Son paket</th>
            <th className="num">Son satış</th>
            <th className="num">Vade</th>
            <th className="num">{kind === "yaklasan" ? "Kalan" : "Geçen"}</th>
            <th className="num" title="Bu müşteriye yapılan abonelik satışı">Satış</th>
          </tr>
        </thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.contact_id}>
              <td className="name">{r.musteri}</td>
              <td>{r.sahip ?? <span className="muted">—</span>}</td>
              <td>{r.paket ?? "—"}</td>
              <td className="num muted">{day(r.son_satis)}</td>
              <td className="num">{day(r.vade)}</td>
              <td className="num">
                {kind === "yaklasan" ? (
                  <span className={`badge ${dueTone(r.kalan_gun ?? 0)}`}>
                    {r.kalan_gun === 0 ? "bugün" : `${int(r.kalan_gun)} gün`}
                  </span>
                ) : (
                  <span className="badge critical">{int(r.gecen_gun)} gün</span>
                )}
              </td>
              <td className="num">{int(r.satis_sayisi)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Churn: iş listeleri (yaklaşan vadeler, kaybedilenler) ve aylık tablo. */
export function ChurnBoard() {
  const [owner, setOwner] = useState("");
  const [view, setView] = useState<View>("yaklasan");
  const { data, error } = usePolling<ChurnPayload>(`/api/churn${owner ? `?owner=${encodeURIComponent(owner)}` : ""}`, 0);
  if (error) return <div className="banner error">Churn verisi alınamadı: {error.message}</div>;
  if (!data) return <div className="skeleton" />;
  if (!data.available) return <div className="empty">Müşteri profilleri ilk saatlik güncellemeyle gelecek.</div>;

  const months = data.aylar ?? [];
  const thisMonth = `${new Date().toISOString().slice(0, 7)}-01`;
  const current = months.find((m) => m.ay === thisMonth);
  // Veri olmayan aylar (CRM öncesi vadeler) tabloyu boş satırla doldurmasın.
  const shown = months.filter((m) => m.satis > 0 || m.vadesi_gelen > 0);
  const refunds = months.reduce((a, m) => (m.ay <= thisMonth ? [a[0] + m.iade, a[1] + m.satis] : a), [0, 0]);
  const upcoming = data.yaklasan ?? [];
  const lost = data.kaybedilen ?? [];
  const due30 = upcoming.filter((r) => (r.kalan_gun ?? 0) <= 30).length;
  const views: [View, string][] = [
    ["yaklasan", `Yaklaşan vadeler · ${upcoming.length}`],
    ["kaybedilen", `Kaybedilenler · ${lost.length}`],
    ["aylik", "Aylık tablo"],
  ];

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
        <StatTile
          label="30 günde vadesi dolan"
          value={int(due30)}
          expected={`${int(upcoming.length)} müşteri ${int(data.yaklasan_gun ?? 60)} günde`}
          tone={due30 ? "warning" : "neutral"}
        />
        <StatTile
          label="Bu ay churn"
          value={rate(current?.churn)}
          expected={
            current
              ? `${int(current.kaybedildi)} kayıp · ${int(current.yeniledi)} yeniledi · ${int(current.bekliyor)} bekliyor`
              : ""
          }
        />
        <StatTile label="Son 12 ay churn" value={rate(data.son_12_ay?.churn)} expected={periodNote(data.son_12_ay)} />
        <StatTile
          label="İade oranı"
          value={refunds[1] ? `%${num((100 * refunds[0]) / refunds[1])}` : "—"}
          expected={`${int(refunds[0])} iade / ${int(refunds[1])} satış · 12 ay`}
        />
      </div>

      <div className="chips churn-views" role="tablist" aria-label="Görünüm">
        {views.map(([k, label]) => (
          <button key={k} role="tab" className={`chip${view === k ? " on" : ""}`} onClick={() => setView(k)} aria-selected={view === k}>
            {label}
          </button>
        ))}
      </div>

      {view === "yaklasan" ? (
        <>
          <p className="muted fn-note">
            Önümüzdeki {int(data.yaklasan_gun ?? 60)} günde aboneliği dolacak ve henüz yenilemeyen müşteriler, vade sırasıyla:
            vadeden önce yenileme araması için.
          </p>
          <ItemTable items={upcoming} kind="yaklasan" />
        </>
      ) : view === "kaybedilen" ? (
        <>
          <p className="muted fn-note">
            Son {int(data.kaybedilen_gun ?? 120)} günde vadesi geçip yenilemeyen ve geri de dönmeyen müşteriler: geri kazanma
            listesi, en yeni kayıp önce.
          </p>
          <ItemTable items={lost} kind="kaybedilen" />
        </>
      ) : (
        <div className="table-wrap">
          <table className="board churn-table">
            <thead>
              <tr>
                <th>Vade ayı</th>
                <th className="num">Vadesi dolan</th>
                <th className="num">Yeniledi</th>
                <th className="num">Kaybedildi</th>
                <th className="num">Churn</th>
                <th className="num" title="Vadeden sonra yeniden satın alan">Geri kazanıldı</th>
                <th className="num" title="Vade günü henüz gelmedi">Bekliyor</th>
                <th className="num">Satış</th>
                <th className="num">İade</th>
                <th className="num">İade oranı</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((m) => {
                const future = m.ay > thisMonth;
                return (
                  <tr key={m.ay} className={future ? "churn-future" : undefined}>
                    <td className="name">
                      {monthFmt.format(new Date(`${m.ay}T00:00:00Z`))}
                      {future ? <span className="sub"> önümüzdeki</span> : null}
                    </td>
                    <td className="num">{m.vadesi_gelen ? int(m.vadesi_gelen) : <span className="muted">—</span>}</td>
                    <td className="num">{m.yeniledi ? int(m.yeniledi) : "—"}</td>
                    <td className="num">{m.kaybedildi ? <b className="warn-text">{int(m.kaybedildi)}</b> : "—"}</td>
                    <td className="num">{m.churn === null ? <span className="muted">—</span> : <b>{rate(m.churn)}</b>}</td>
                    <td className="num">{m.geri_kazanildi ? int(m.geri_kazanildi) : "—"}</td>
                    <td className="num muted">{m.bekliyor ? int(m.bekliyor) : "—"}</td>
                    <td className="num">{m.satis ? int(m.satis) : "—"}</td>
                    <td className="num">{m.iade ? int(m.iade) : "—"}</td>
                    <td className="num">{rate(m.iade_orani)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted fn-note">
        Churn: aboneliği (satıştan 12 ay) o ay dolan müşterilerden vade gününe kadar yeni abonelik satışı olmayanlar; ek süre
        yok. Oran yalnız vadesi geçmiş olanlar üzerinden (bekleyenler hariç). Abonelik satışı: {data.packages?.join(", ")}.
        Ölçüm CRM'e geçişten (Ekim 2025) itibaren satın alanlar için yapılabiliyor; CRM öncesi müşterilerin vadeleri bu
        listelere girmez.
      </p>
    </>
  );
}
