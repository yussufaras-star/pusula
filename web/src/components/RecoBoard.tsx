import { useState } from "react";

import { usePolling } from "../api";
import { int, num, tl } from "../format";
import type { RecoPayload } from "../types";

const LIST_SHOWN = 60;
const day = new Intl.DateTimeFormat("tr-TR", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "UTC" });

function when(gun: number | null): { text: string; cls: string } {
  if (gun === null) return { text: "tarih yok", cls: "muted" };
  if (gun < 0) return { text: `${int(-gun)} gün önce bitti`, cls: gun >= -30 ? "warn-text" : "muted" };
  if (gun <= 30) return { text: `${int(gun)} gün kaldı`, cls: "warn-text" };
  return { text: `${int(gun)} gün kaldı`, cls: "muted" };
}

/** Re-Co yenileme: gruplar, bu ayın Re-Co satışları ve satıp yenilememişler listesi. */
export function RecoBoard() {
  const [owner, setOwner] = useState<string>("");
  const [showAll, setShowAll] = useState(false);
  const { data, error } = usePolling<RecoPayload>(`/api/reco${owner ? `?owner=${encodeURIComponent(owner)}` : ""}`, 0);

  if (error) return <div className="banner error">Re-Co verisi alınamadı: {error.message}</div>;
  if (!data) return <div className="skeleton" />;
  if (!data.available) return <div className="empty">Müşteri profilleri ilk saatlik güncellemeyle gelecek.</div>;

  const segments = data.segments ?? [];
  const list = data.liste ?? [];
  const shown = showAll ? list : list.slice(0, LIST_SHOWN);
  const owners = (data.owners ?? []).slice(0, 5);

  return (
    <>
      <div className="ss-controls">
        <div className="chips" role="group" aria-label="Müşteri sahibi">
          <button className={`chip${owner === "" ? " on" : ""}`} onClick={() => setOwner("")} aria-pressed={owner === ""}>
            Tüm müşteriler
          </button>
          {owners.map(([name, n]) => (
            <button
              key={name}
              className={`chip${owner === name ? " on" : ""}`}
              onClick={() => setOwner(name)}
              aria-pressed={owner === name}
            >
              {name} <span className="muted">{int(n)}</span>
            </button>
          ))}
        </div>
        {data.bu_ay?.length ? (
          <span className="muted">
            Bu ay Re-Co:{" "}
            {data.bu_ay.map((m) => `${m.owner} ${int(m.satis)} satış · ${tl(m.ciro)}`).join(" · ")}
          </span>
        ) : null}
      </div>

      <div className="table-wrap">
        <table className="board reco-table">
          <thead>
            <tr>
              <th>Grup</th>
              <th className="num">Müşteri</th>
              <th className="num" title="Abonelik bitiş tarihi geçmiş">Aboneliği bitmiş</th>
              <th className="num">30 gün içinde bitecek</th>
              <th className="num">31–90 gün</th>
              <th className="num">Abonelik tarihi yok</th>
              <th className="num">Re-Co satış</th>
              <th className="num">Re-Co ciro</th>
              <th className="num" title="Müşterinin kendi mağazasında son 1 yıllık satış adedi, medyan">Medyan son 1 yıl satış</th>
            </tr>
          </thead>
          <tbody>
            {segments.map((s) => (
              <tr key={s.key} className={s.key === "satiyor_yenilememis" ? "reco-focus" : undefined}>
                <td className="name">{s.label}</td>
                <td className="num">
                  <b>{int(s.musteri)}</b>
                  {data.toplam ? <div className="sub">%{num((100 * s.musteri) / data.toplam)}</div> : null}
                </td>
                <td className="num">{int(s.abonelik_bitmis)}</td>
                <td className="num">{s.bitis_30 ? <b className="warn-text">{int(s.bitis_30)}</b> : "—"}</td>
                <td className="num">{int(s.bitis_90)}</td>
                <td className="num muted">{int(s.abonelik_yok)}</td>
                <td className="num">{int(s.reco_satis)}</td>
                <td className="num">{tl(s.reco_ciro)}</td>
                <td className="num">{s.medyan_son1 === null ? "—" : int(s.medyan_son1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted fn-note">
        Müşteri: en az bir kazanılmış satışı olan kişi. Satıyor: Zoho'da "Son 1 Yıllık Satış" &gt; 0. Yenilemiş: en az bir
        kazanılmış Re-Co-Premium satışı var.
      </p>

      <h3 className="reco-h3">
        Re-Co fırsatları: satıyor ama yenilememiş ({int(data.liste_toplam ?? 0)})
        <span className="muted"> · aboneliği 30 gün önce bitmiş ile 60 gün sonra bitecek arası üstte</span>
      </h3>
      {list.length ? (
        <div className="table-wrap">
          <table className="board">
            <thead>
              <tr>
                <th>Müşteri</th>
                <th>Sahibi</th>
                <th>Paket</th>
                <th>Abonelik bitiş</th>
                <th className="num">Son 1 yıl satış</th>
                <th className="num">Toplam satış</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((r) => {
                const w = when(r.gun);
                return (
                  <tr key={r.contact_id}>
                    <td className="name">{r.name ?? "İsimsiz"}</td>
                    <td>{r.owner ?? "—"}</td>
                    <td>{r.paket ?? "—"}</td>
                    <td>
                      {r.abonelik_bit ? day.format(new Date(`${r.abonelik_bit}T00:00:00Z`)) : "—"}
                      <span className={`sub ${w.cls}`}> · {w.text}</span>
                    </td>
                    <td className="num">{int(r.son1_satis)}</td>
                    <td className="num">{int(r.toplam_satis)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">Satıp yenilememiş müşteri yok.</div>
      )}
      {list.length > LIST_SHOWN ? (
        <button className="link-btn" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Daha az göster" : `Tümünü göster (${int(list.length)})`}
        </button>
      ) : null}
    </>
  );
}
