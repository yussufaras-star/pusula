import { Fragment, useState } from "react";

import { clock, int, minutes, pct } from "../format";
import type { MeetingItem, MeetingState, Meetings, RepCard } from "../types";

const STATE_LABEL: Record<MeetingState, string> = {
  geldi: "Geldi",
  gelmedi: "Gelmedi",
  bekleniyor: "Bekleniyor",
  suruyor: "Sürüyor",
  bilinmiyor: "Bilinmiyor",
  iptal: "İptal",
};

const CRM_LABEL: Record<string, string> = {
  katildi: "Katıldı",
  katilmadi: "Katılmadı",
  iptal_edildi: "İptal",
  sonuc_girilmedi: "Girilmemiş",
};

export function MeetingBoard({ meetings, reps }: { meetings: Meetings; reps: RepCard[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const rows = reps
    .map((r) => ({ rep: r, s: meetings.by_rep[r.rep_id] }))
    .filter((x) => x.s && (x.s.planlanan || x.s.iptal))
    .sort((a, b) => b.s.planlanan - a.s.planlanan || a.rep.name.localeCompare(b.rep.name, "tr"));

  if (!rows.length) return <div className="empty">Bugün planlanmış toplantı yok.</div>;

  return (
    <div className="table-wrap">
      <table className="board meet-board">
        <thead>
          <tr>
            <th>Temsilci</th>
            <th className="num">Planlanan</th>
            <th className="num">Gelen</th>
            <th className="num">Gelmeyen</th>
            <th className="num">Katılım</th>
            <th className="num" title="Saati geçmiş, CRM'e sonucu girilmemiş">Sonuç girilmemiş</th>
            <th className="num" title="Müşteriyle birlikte + en fazla 10 dk bekleme">Toplantı süresi</th>
            <th>Gelmeyen arandı mı</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ rep, s }) => {
            const isOpen = open === rep.rep_id;
            const items = meetings.items.filter((m) => m.rep_id === rep.rep_id);
            return (
              <Fragment key={rep.rep_id}>
                <tr className="clickable" onClick={() => setOpen(isOpen ? null : rep.rep_id)} aria-expanded={isOpen}>
                  <td>
                    <div className="name">
                      <span className="caret" aria-hidden>
                        {isOpen ? "▾" : "▸"}
                      </span>{" "}
                      {rep.name}
                    </div>
                    {s.uyumsuz ? (
                      <div className="sub warn-text" title="CRM sonucu ile Zoho Meeting katılımı farklı">
                        {int(s.uyumsuz)} kayıt CRM ile uyuşmuyor
                      </div>
                    ) : null}
                  </td>
                  <td className="num">
                    {int(s.planlanan)}
                    {s.iptal ? <div className="sub">{int(s.iptal)} iptal</div> : null}
                  </td>
                  <td className="num">{int(s.gelen)}</td>
                  <td className="num">{int(s.gelmeyen)}</td>
                  <td className="num">
                    {pct(s.katilim_orani)}
                    {s.bilinmiyor ? <div className="sub">{int(s.bilinmiyor)} bilinmiyor</div> : null}
                  </td>
                  <td className={`num ${s.sonuc_girilmemis ? "warn-text" : ""}`}>{int(s.sonuc_girilmemis)}</td>
                  <td className="num">{minutes(s.toplam_dk)}</td>
                  <td>
                    {s.gelmeyen ? (
                      <>
                        <b>{int(s.aranan)}</b> arandı
                        {s.aranmayan ? (
                          <span className="warn-text">
                            {" "}
                            · <b>{int(s.aranmayan)}</b> aranmadı
                          </span>
                        ) : null}
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
                {isOpen ? (
                  <tr className="detail">
                    <td colSpan={8}>
                      <MeetingList items={items} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Geldi / gelmedi önce Zoho Meeting'den (müşteri odada en az 1 dk), yoksa CRM sonucundan. Süre: müşteriyle
        birlikte geçen süre + en fazla 10 dk bekleme. "Arandı": toplantı saatinden sonra aynı müşteriye giden arama
        veya WhatsApp.
      </p>
    </div>
  );
}

function MeetingList({ items }: { items: MeetingItem[] }) {
  return (
    <table className="meet-list">
      <thead>
        <tr>
          <th>Saat</th>
          <th>Müşteri</th>
          <th>Durum</th>
          <th className="num">Süre</th>
          <th>CRM sonucu</th>
          <th>Takip</th>
        </tr>
      </thead>
      <tbody>
        {items.map((m, i) => (
          <tr key={i}>
            <td className="tabular">{clock(m.start)}</td>
            <td>{m.customer ?? <span className="muted">—</span>}</td>
            <td>
              <span className={`mstate ${m.state}`}>{STATE_LABEL[m.state]}</span>
              {m.mismatch ? <span className="sub warn-text"> · CRM farklı</span> : null}
            </td>
            <td className="num">{m.minutes ? minutes(m.minutes) : "—"}</td>
            <td className={m.result_entered ? undefined : "warn-text"}>{CRM_LABEL[m.crm_status] ?? m.crm_status}</td>
            <td>
              {m.state !== "gelmedi" ? (
                <span className="muted">—</span>
              ) : m.call_after ? (
                <>arandı {clock(m.call_after)}</>
              ) : m.wa_after ? (
                <>WhatsApp {clock(m.wa_after)}</>
              ) : (
                <span className="warn-text">aranmadı</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
