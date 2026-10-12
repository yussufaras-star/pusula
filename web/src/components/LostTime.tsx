import { useMemo } from "react";

import { clock, minutes, pct } from "../format";
import type { RepCard, SegmentKind } from "../types";

const KIND_LABEL: Record<SegmentKind, string> = {
  dolu: "Dolu",
  toplanti: "Toplantı",
  mola: "Mola",
  kayip: "Kayıp",
  girilmemis: "Sonucu girilmemiş toplantı",
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

interface Props {
  reps: RepCard[];
  day: string;
  now: string | null;
}

export function LostTime({ reps, day, now }: Props) {
  const [startH, endH] = shiftHours(day);
  const lo = startH * 60;
  const span = (endH - startH) * 60;
  const pos = (iso: string) => ((Math.min(Math.max(minuteOfDay(iso), lo), lo + span) - lo) / span) * 100;
  const nowPos = now ? pos(now) : 100;

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
      <div className="legend" style={{ marginBottom: 10 }}>
        {(["dolu", "toplanti", "mola", "kayip", "girilmemis"] as SegmentKind[]).map((k) => (
          <span key={k} className="key">
            <span className={`sw tl-${k}`} /> {KIND_LABEL[k]}
          </span>
        ))}
        {now ? (
          <span className="key">
            <span className="sw tl-future" /> Henüz gelmedi
          </span>
        ) : null}
      </div>
      <div className="lost-grid">
        {rows.map((r) => {
          const lost = r.gaps.filter((g) => g.kind !== "mola").slice(0, 3);
          return (
            <div key={r.rep_id} className="lost-row">
              <div className="lost-name">
                <div className="name">{r.name}</div>
                <div className="sub">
                  doluluk <b>{pct(r.today.doluluk)}</b>
                </div>
              </div>
              <div className="lost-bar-wrap">
                <div className="lost-bar" role="img" aria-label={`${r.name} mesai şeridi`}>
                  {r.timeline.map((s, i) => (
                    <span
                      key={i}
                      className={`tl tl-${s.kind}`}
                      style={{ left: `${pos(s.start)}%`, width: `${Math.max(pos(s.end) - pos(s.start), 0.15)}%` }}
                      title={`${KIND_LABEL[s.kind]} · ${clock(s.start)}–${clock(s.end)} · ${minutes(
                        (new Date(s.end).getTime() - new Date(s.start).getTime()) / 60000,
                      )}`}
                    />
                  ))}
                  {nowPos < 100 ? (
                    <span className="tl tl-future" style={{ left: `${nowPos}%`, width: `${100 - nowPos}%` }} />
                  ) : null}
                </div>
                <div className="lost-gaps">
                  {lost.length ? (
                    lost.map((g) => (
                      <span key={g.start} className={g.kind === "girilmemis" ? "gap-meet" : undefined}>
                        {clock(g.start)}–{clock(g.end)} <b>{minutes(g.minutes)}</b>
                        {g.kind === "girilmemis" ? " · sonucu girilmemiş toplantı" : ""}
                      </span>
                    ))
                  ) : (
                    <span className="muted">5 dk üstü kayıp boşluk yok</span>
                  )}
                </div>
              </div>
              <div className="lost-sum">
                <b>{minutes(r.today.kayip_dk)}</b>
                <span className="sub">kayıp · {minutes(r.today.mola_dk)} mola</span>
                {r.today.toplanti_dk > 0 ? (
                  <span className="sub">{minutes(r.today.toplanti_dk)} toplantı (dolu)</span>
                ) : null}
                {r.today.girilmemis_dk > 0 ? (
                  <span className="sub">{minutes(r.today.girilmemis_dk)}'sı sonucu girilmemiş toplantı</span>
                ) : null}
              </div>
            </div>
          );
        })}
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
