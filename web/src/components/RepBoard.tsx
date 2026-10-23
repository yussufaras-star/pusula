import { Fragment, useMemo, useState } from "react";

import { ago, clock, duration, hourLabel, int, minutes, num, pace, paceTone, pct } from "../format";
import type { Excused, MeetingSummary, RepCard, RepStatus } from "../types";
import { LostLegend, LostStrip } from "./LostTime";

const STATUS_LABEL: Record<RepStatus, string> = {
  aktif: "Aktif",
  toplantida: "Toplantıda",
  aramada: "Aramada",
  bosta: "Boşta",
  hareketsiz: "Hareketsiz",
  baslamadi: "Başlamadı",
  mesai_disi: "Mesai dışı",
  gecmis: "Geçmiş gün",
  izinli: "İzinli",
  muaf: "Muaf",
};

const STATUS_ORDER: Record<RepStatus, number> = {
  hareketsiz: 0,
  bosta: 1,
  baslamadi: 2,
  aktif: 3,
  aramada: 4,
  toplantida: 5,
  mesai_disi: 7,
  gecmis: 8,
  muaf: 6,
  izinli: 9,
};

export function StatusPill({ status }: { status: RepStatus }) {
  return (
    <span className={`status ${status}`}>
      <span className={`dot ${status}`} aria-hidden />
      {STATUS_LABEL[status]}
    </span>
  );
}

export function PaceMeter({ actual, expected }: { actual: number; expected: number }) {
  const ratio = pace(actual, expected);
  const tone = paceTone(ratio);
  const scaleMax = Math.max(actual, expected, 1) * 1.15;
  return (
    <div className="meter" title={`Gerçekleşen ${int(actual)} · beklenen ${num(expected)}`}>
      <div
        className={`fill ${tone === "neutral" ? "" : tone}`}
        style={{ width: `${(actual / scaleMax) * 100}%` }}
      />
      {expected > 0 ? (
        <div className="mark" style={{ left: `calc(${(expected / scaleMax) * 100}% - 1px)` }} />
      ) : null}
    </div>
  );
}

function heat(v: number, max: number): string {
  if (v <= 0) return "";
  const q = v / Math.max(max, 1);
  return q > 0.75 ? "h4" : q > 0.5 ? "h3" : q > 0.25 ? "h2" : "h1";
}

function HourStrip({ card, nowHour }: { card: RepCard; nowHour: number | null }) {
  const max = Math.max(...card.hours.map((h) => Math.max(h.today.arama, h.expected.arama)), 1);
  return (
    <div className="strip" aria-label="Saatlik arama">
      {card.hours.map((h) => {
        const future = nowHour !== null && h.hour > nowHour;
        return (
          <span
            key={h.hour}
            className={future ? "future" : heat(h.today.arama, max)}
            title={`${hourLabel(h.hour)} · ${int(h.today.arama)} arama (ort. ${num(h.expected.arama)})`}
          />
        );
      })}
    </div>
  );
}

interface Props {
  reps: RepCard[];
  nowHour: number | null;
  warnIdle: number;
  crmTracking: boolean;
  now: Date;
  onLeave?: (repId: string, on: boolean) => void;
  onExcuse?: ExcuseHandlers;
  meetings?: Record<string, MeetingSummary>;
  /** Verilirse her temsilcinin altında mesai şeridi (kayıp zaman) gösterilir. */
  strip?: { day: string; now: string | null };
}

export interface ExcuseHandlers {
  add: (repId: string, start: string, end: string, reason: string) => Promise<string | null>;
  remove: (id: number) => void;
}

/** Muaf süreler (eğitim vb.) ve ekleme formu; yalnız yönetici. */
function ExcusedCell({ card, handlers }: { card: RepCard; handlers: ExcuseHandlers }) {
  const [open, setOpen] = useState(false);
  const [start, setStart] = useState("10:00");
  const [end, setEnd] = useState("11:00");
  const [reason, setReason] = useState("Eğitim");
  const [error, setError] = useState<string | null>(null);

  async function save() {
    const err = await handlers.add(card.rep_id, start, end, reason);
    setError(err);
    if (!err) setOpen(false);
  }

  return (
    <div className="excused">
      {card.excused.map((x: Excused) => (
        <div key={x.id} className="sub" title="Bu aralık kayıp ve mola sayılmaz">
          {clock(x.start)}–{clock(x.end)} {x.reason ?? "muaf"}{" "}
          <button className="link-btn" aria-label="Muaf süreyi sil" onClick={() => handlers.remove(x.id)}>
            ×
          </button>
        </div>
      ))}
      {open ? (
        <div className="excused-form">
          <input type="time" value={start} onChange={(e) => setStart(e.target.value)} aria-label="Başlangıç" />
          <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} aria-label="Bitiş" />
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Neden" aria-label="Neden" maxLength={80} />
          <button className="link-btn" onClick={save}>kaydet</button>
          <button className="link-btn" onClick={() => setOpen(false)}>vazgeç</button>
          {error ? <div className="sub warn-text">{error}</div> : null}
        </div>
      ) : (
        <button className="link-btn" onClick={() => setOpen(true)} title="Eğitim, iç toplantı gibi doluluğa girmeyecek süre">
          muaf süre ekle
        </button>
      )}
    </div>
  );
}

export function RepBoard({ reps, nowHour, warnIdle, crmTracking, now, onLeave, onExcuse, meetings, strip }: Props) {
  const rows = useMemo(
    () =>
      [...reps].sort(
        (a, b) =>
          STATUS_ORDER[a.status] - STATUS_ORDER[b.status] ||
          (b.idle_min ?? 0) - (a.idle_min ?? 0) ||
          a.name.localeCompare(b.name, "tr"),
      ),
    [reps],
  );

  if (!rows.length) return <div className="empty">Temsilci yok.</div>;

  return (
    <div className="table-wrap">
      {strip ? <LostLegend future={!!strip.now} /> : null}
      <table className={`board${strip ? " with-strip" : ""}`}>
        <thead>
          <tr>
            <th>Temsilci</th>
            <th>Durum</th>
            <th className="num">Hareketsiz</th>
            <th>Son faaliyet</th>
            <th>Son CRM işlemi</th>
            <th>Arama · tempo</th>
            <th className="num">Ulaşılan</th>
            <th className="num">Ulaşma</th>
            <th className="num" title="Bugünkü toplantılar (iptal hariç); gelen bilgisi Toplantılar tablosuyla aynı">Toplantı</th>
            <th className="num">Ort. görüşme</th>
            <th>Doluluk</th>
            <th>Saatlik arama</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const idleAlert = r.idle_min !== null && r.idle_min >= warnIdle;
            return (
              <Fragment key={r.rep_id}>
              <tr className={strip ? "has-strip" : undefined}>
                <td>
                  <div className="name">{r.name}</div>
                  <div className="sub">ilk faaliyet {clock(r.first_at)}</div>
                  {onLeave ? (
                    <button className="link-btn" onClick={() => onLeave(r.rep_id, !r.on_leave)}>
                      {r.on_leave ? "izni geri al" : "izinli yap"}
                    </button>
                  ) : null}
                  {onExcuse && !r.on_leave ? <ExcusedCell card={r} handlers={onExcuse} /> : null}
                </td>
                <td>
                  <StatusPill status={r.status} />
                </td>
                <td className={`num ${idleAlert ? "idle-alert" : ""}`}>{minutes(r.idle_min)}</td>
                <td>
                  <div>{clock(r.last_active_at)}</div>
                  <div className="sub">{lastKind(r)}</div>
                </td>
                <td>
                  {r.last_crm_at ? (
                    <>
                      <div>{clock(r.last_crm_at)}</div>
                      <div className="sub">{ago(r.last_crm_at, now)}</div>
                    </>
                  ) : crmTracking ? (
                    <span className="muted">bugün yok</span>
                  ) : (
                    <span className="muted" title="CRM işlem akışı ilk çalıştığında dolacak">
                      veri bekleniyor
                    </span>
                  )}
                </td>
                <td className="pace-cell">
                  <div className="row">
                    <b>{int(r.today.arama)}</b>
                    <span className="muted">
                      beklenen {num(r.target?.arama?.by_now ?? r.expected.arama)}
                      {r.target?.arama && r.target.arama.source !== "ortalama" ? ` / ${int(r.target.arama.daily)}` : ""}
                    </span>
                  </div>
                  <PaceMeter actual={r.today.arama} expected={r.target?.arama?.by_now ?? r.expected.arama} />
                </td>
                <td className="num">{int(r.today.ulasilan)}</td>
                <td className="num">{pct(r.today.ulasma_orani)}</td>
                <td className="num">
                  {meetings?.[r.rep_id] ? (
                    <>
                      {int(meetings[r.rep_id].planlanan)}
                      <div className="sub">{int(meetings[r.rep_id].gelen)} geldi</div>
                    </>
                  ) : (
                    int(r.today.randevu)
                  )}
                </td>
                <td className="num">{duration(r.today.sure_ort)}</td>
                <td className="pace-cell" style={{ minWidth: 110 }}>
                  <div className="row">
                    <b>{pct(r.today.doluluk)}</b>
                  </div>
                  <div className="meter">
                    <div className="fill" style={{ width: `${Math.min(r.today.doluluk ?? 0, 100)}%` }} />
                  </div>
                  <div className="sub" title="Mola dışı kayıp zaman" style={{ marginTop: 4 }}>
                    {minutes(r.today.kayip_dk)} kayıp
                  </div>
                </td>
                <td>
                  <HourStrip card={r} nowHour={nowHour} />
                </td>
              </tr>
              {strip ? (
                <tr className="strip-row">
                  <td colSpan={12}>
                    {r.on_leave ? (
                      <span className="muted">İzinli — mesai şeridi ölçüme girmez</span>
                    ) : r.timeline.length ? (
                      <LostStrip rep={r} day={strip.day} now={strip.now} ticks />
                    ) : (
                      <span className="muted">Mesai şeridi yok</span>
                    )}
                  </td>
                </tr>
              ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Hareketsizlik: mesai içinde son arama, toplantı, WhatsApp veya CRM işleminden bu yana
        geçen süre; süren toplantı hareketsizlik sayılmaz. İzinli gün doluluğa ve ekip beklentisine girmez; muaf süre (eğitim vb.) kayıp ve mola sayılmaz, o sırada dürtme gitmez. Veri tazeliğiyle sınırlıdır. Tempo çizgisi: son 90 günde aynı gün tipinde bu
        saate kadar yapılan ortalama.
      </p>
    </div>
  );
}

function lastKind(r: RepCard): string {
  const stamps: [string, string | null][] = [
    ["arama", r.last_call_at],
    ["toplantı", r.last_meeting_at],
    ["WhatsApp", r.last_whatsapp_at],
    ["CRM", r.last_crm_at],
  ];
  const best = stamps
    .filter((s): s is [string, string] => s[1] !== null)
    .sort((a, b) => b[1].localeCompare(a[1]))[0];
  return best ? best[0] : "faaliyet yok";
}
