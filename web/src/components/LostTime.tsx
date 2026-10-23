import { useMemo } from "react";

import { clock, minutes, pct } from "../format";
import type { RepCard, SegmentKind } from "../types";

const KIND_LABEL: Record<SegmentKind, string> = {
  dolu: "Dolu",
  toplanti: "Toplantı",
  mola: "Mola",
  kayip: "Kayıp",
  girilmemis: "Sonucu girilmemiş toplantı",
  muaf: "Muaf (eğitim vb.)",
};

/** Mesai: hafta içi 09-18, cumartesi 09-15 (pusula.metrics ile aynı). */
function shiftHours(day: string): [number, number] {
  const dow = new Date(`${day}T12:00:00Z`).getUTCDay();
  return [9, dow === 6 ? 15 : 18];
}

function minuteOfDay(iso: string): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "Europe/Istanbul",
  }).formatToParts(new Date(iso));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return get("hour") * 60 + get("minute") + get("second") / 60;
}

/** Şeritteki mola dilimleri; bitişik dilimler tek aralık. 1 dk altı kırıntılar atlanır. */
function breakIntervals(timeline: RepCard["timeline"]): { start: string; end: string; min: number }[] {
  const out: { start: string; end: string; min: number }[] = [];
  for (const s of [...timeline].filter((t) => t.kind === "mola").sort((a, b) => a.start.localeCompare(b.start))) {
    const last = out[out.length - 1];
    if (last && new Date(s.start).getTime() - new Date(last.end).getTime() <= 60_000) {
      last.end = s.end;
    } else {
      out.push({ start: s.start, end: s.end, min: 0 });
    }
  }
  for (const b of out) b.min = (new Date(b.end).getTime() - new Date(b.start).getTime()) / 60000;
  return out.filter((b) => b.min >= 1);
}

interface Props {
  reps: RepCard[];
  day: string;
  now: string | null;
}

export function LostLegend({ future }: { future: boolean }) {
  return (
    <div className="legend" style={{ marginBottom: 10 }}>
      {(["dolu", "toplanti", "mola", "kayip", "girilmemis", "muaf"] as SegmentKind[]).map((k) => (
        <span key={k} className="key">
          <span className={`sw tl-${k}`} /> {KIND_LABEL[k]}
        </span>
      ))}
      {future ? (
        <span className="key">
          <span className="sw tl-future" /> Henüz gelmedi
        </span>
      ) : null}
    </div>
  );
}

/** Tek temsilcinin mesai şeridi ve mola aralıkları; ticks ile saat etiketleri şeridin altında. */
export function LostStrip({ rep, day, now, ticks = false }: { rep: RepCard; day: string; now: string | null; ticks?: boolean }) {
  const [startH, endH] = shiftHours(day);
  const lo = startH * 60;
  const span = (endH - startH) * 60;
  const pos = (iso: string) => ((Math.min(Math.max(minuteOfDay(iso), lo), lo + span) - lo) / span) * 100;
  const nowPos = now ? pos(now) : 100;
  const breaks = breakIntervals(rep.timeline);
  return (
    <div className="lost-bar-wrap">
      <div className="lost-bar" role="img" aria-label={`${rep.name} mesai şeridi`}>
        {rep.timeline.map((s, i) => (
          <span
            key={i}
            className={`tl tl-${s.kind}`}
            style={{ left: `${pos(s.start)}%`, width: `${Math.max(pos(s.end) - pos(s.start), 0.15)}%` }}
            title={`${KIND_LABEL[s.kind]} · ${clock(s.start)}–${clock(s.end)} · ${minutes(
              (new Date(s.end).getTime() - new Date(s.start).getTime()) / 60000,
            )}`}
          />
        ))}
        {nowPos < 100 ? <span className="tl tl-future" style={{ left: `${nowPos}%`, width: `${100 - nowPos}%` }} /> : null}
      </div>
      {ticks ? (
        <div className="lost-ticks" aria-hidden>
          {Array.from({ length: endH - startH + 1 }, (_, i) => startH + i).map((h) => (
            <span key={h} style={{ left: `${((h - startH) / (endH - startH)) * 100}%` }}>
              {String(h).padStart(2, "0")}
            </span>
          ))}
        </div>
      ) : null}
      <div className="lost-gaps" title="Günün en uzun boşluklarından mola olarak düşülen aralıklar">
        {breaks.length ? (
          <>
            <span className="muted">Mola</span>
            {breaks.map((b) => (
              <span key={b.start}>
                {clock(b.start)}–{clock(b.end)} <b>{minutes(b.min)}</b>
              </span>
            ))}
            <span className="muted">toplam {minutes(rep.today.mola_dk)}</span>
          </>
        ) : (
          <span className="muted">Henüz mola düşülmedi</span>
        )}
      </div>
    </div>
  );
}

export function LostTime({ reps, day, now }: Props) {
  const [startH, endH] = shiftHours(day);

  const rows = useMemo(
    () =>
      [...reps]
        .filter((r) => r.timeline.length && !r.on_leave)
        .sort((a, b) => b.today.kayip_dk - a.today.kayip_dk || a.name.localeCompare(b.name, "tr")),
    [reps],
  );

  const onLeave = reps.filter((r) => r.on_leave).map((r) => r.name);

  if (!rows.length) return <div className="empty">Bu gün için mesai şeridi yok.</div>;

  const ticks = Array.from({ length: endH - startH + 1 }, (_, i) => startH + i);

  return (
    <div className="lost">
      <LostLegend future={!!now} />
      <div className="lost-grid">
        {rows.map((r) => (
            <div key={r.rep_id} className="lost-row">
              <div className="lost-name">
                <div className="name">{r.name}</div>
                <div className="sub">
                  doluluk <b>{pct(r.today.doluluk)}</b>
                </div>
              </div>
              <LostStrip rep={r} day={day} now={now} />
              <div
                className="lost-sum"
                title={`Mola olarak düşülen: ${minutes(r.today.mola_dk)}${
                  r.today.toplanti_dk > 0 ? ` · toplantıda geçen: ${minutes(r.today.toplanti_dk)}` : ""
                }${r.today.girilmemis_dk > 0 ? ` · sonucu girilmemiş toplantı: ${minutes(r.today.girilmemis_dk)}` : ""}${
                  r.today.muaf_dk > 0 ? ` · muaf (doluluğa girmez): ${minutes(r.today.muaf_dk)}` : ""
                }`}
              >
                <b>{minutes(r.today.kayip_dk)}</b>
                <span className="sub">kayıp zaman</span>
              </div>
            </div>
        ))}
        {onLeave.length ? (
          <div className="muted" style={{ fontSize: 12.5 }}>
            İzinli (ölçüme girmez): {onLeave.join(", ")}
          </div>
        ) : null}
        <div className="lost-row axis" aria-hidden>
          <div />
          <div className="lost-ticks">
            {ticks.map((h) => (
              <span key={h} style={{ left: `${((h - startH) / (endH - startH)) * 100}%` }}>
                {String(h).padStart(2, "0")}
              </span>
            ))}
          </div>
          <div />
        </div>
      </div>
    </div>
  );
}
