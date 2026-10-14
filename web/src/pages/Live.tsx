import { useState } from "react";

import { api, usePolling } from "../api";
import { Attention } from "../components/Attention";
import { Commentary } from "../components/Commentary";
import { CommissionCard } from "../components/CommissionCard";
import { HourChart } from "../components/HourChart";
import { LostTime } from "../components/LostTime";
import { MeetingBoard } from "../components/MeetingBoard";
import { NudgePanel } from "../components/NudgePanel";
import { PortfolioCard } from "../components/PortfolioBoard";
import { RepBoard, StatusPill } from "../components/RepBoard";
import { StatTile } from "../components/StatTile";
import { SyncButton } from "../components/SyncButton";
import { ago, dayLabel, duration, int, minutes, num, pace, paceTone, pct, type Tone } from "../format";
import type { Kpis, LivePayload, Me, TargetItem, Targets } from "../types";

const REFRESH_MS = 60_000;
const STALE_MIN = 20;

function istanbulToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
}

function istanbulHour(iso: string): number {
  return Number(
    new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hour12: false, timeZone: "Europe/Istanbul" }).format(
      new Date(iso),
    ),
  );
}

/** Hedef varsa kart günlük hedefe ve "bu saate kadar" payına göre boyanır. */
function targetNote(t: TargetItem): string {
  const basis = t.source === "ortalama" ? "ortalama" : "hedef";
  return `${basis} ${int(t.daily)} · şimdiye ${num(t.by_now)}`;
}

const TARGET_RULE: Partial<Record<keyof Targets, string>> = {
  arama:
    "Hedef: arama blokları (09-11, 14-17) 300 dk − 75 dk mola, görüşme başına 5 dk → 45. Gelmeyen toplantının 10 dk sonrası arama süresi sayılır ve hedefe eklenir.",
  ulasilan: "Hedef: arama hedefinin %70'i.",
  randevu: "Hedef: aynı gün tipinin son 90 günlük ortalaması; toplantı bloklarında (11-14, 17-18) birikir.",
};

function countTile(label: string, key: keyof Kpis & keyof Targets, t: Kpis, e: Kpis, targets?: Targets) {
  const actual = Number(t[key] ?? 0);
  const target = targets?.[key];
  const expected = target ? target.by_now : Number(e[key] ?? 0);
  const ratio = pace(actual, expected);
  const tone = paceTone(ratio);
  return (
    <StatTile
      label={label}
      value={int(actual)}
      expected={target ? targetNote(target) : `beklenen ${num(expected)}`}
      tone={tone}
      badge={ratio === null ? undefined : `%${Math.round(ratio * 100)}`}
      title={
        target
          ? `${TARGET_RULE[key] ?? ""} Bu saate kadar ${num(target.by_now)} beklenir.${
              target.bonus ? ` Gelmeyen toplantılardan +${num(target.bonus)} arama eklendi.` : ""
            }`
          : undefined
      }
    />
  );
}

function rateTile(label: string, actual: number | null, expected: number | null, sample: number, isTarget = false) {
  const diff = actual !== null && expected !== null ? actual - expected : null;
  const tone: Tone =
    diff === null || sample < 5 ? "neutral" : diff >= 0 ? "good" : diff > -10 ? "warning" : "critical";
  return (
    <StatTile
      label={label}
      value={sample < 5 && actual !== null ? <span title="Örneklem 5'ten az">{pct(actual)}</span> : pct(actual)}
      expected={sample < 5 ? `örneklem ${sample} · az` : `${isTarget ? "hedef" : "ortalama"} ${pct(expected)}`}
      tone={tone}
      badge={diff === null ? undefined : `${diff >= 0 ? "+" : ""}${num(diff)} puan`}
    />
  );
}

export function LivePage({ me }: { me: Me }) {
  const [day, setDay] = useState<string>(istanbulToday());
  const today = istanbulToday();
  const path = `/api/live${day === today ? "" : `?day=${day}`}`;
  const { data, error, fetchedAt, refresh, loading } = usePolling<LivePayload>(path, day === today ? REFRESH_MS : 0);
  const now = fetchedAt ?? new Date();
  const [syncTick, setSyncTick] = useState(0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{me.role === "admin" ? "Canlı operasyon" : "Bugünüm"}</h1>
          <div className="sub">
            {data ? dayLabel(data.day) : "…"}
            {data ? ` · ${data.history_days} benzer günle kıyas` : ""}
          </div>
        </div>
        <div className="controls">
          <Freshness data={data} now={now} loading={loading} />
          <input
            type="date"
            value={day}
            max={today}
            onChange={(e) => e.target.value && setDay(e.target.value)}
            aria-label="Gün"
          />
          {day !== today ? (
            <button className="btn" onClick={() => setDay(today)}>
              Bugün
            </button>
          ) : me.role === "admin" ? (
            <SyncButton
              onDone={() => {
                refresh();
                setSyncTick((n) => n + 1);
              }}
            />
          ) : (
            <button className="btn" onClick={refresh}>
              Yenile
            </button>
          )}
        </div>
      </div>

      {error ? (
        <div className="banner error">
          Veri alınamadı: {error.message}
          {error.status === 503 ? " (sunucu yapılandırması eksik)" : ""}
        </div>
      ) : null}

      {!data ? (
        <div className="tiles">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="skeleton" />
          ))}
        </div>
      ) : (
        <LiveBody data={data} me={me} refresh={refresh} syncTick={syncTick} />
      )}
    </>
  );
}

function LiveBody({
  data,
  me,
  refresh,
  syncTick,
}: {
  data: LivePayload;
  me: Me;
  refresh: () => void;
  syncTick: number;
}) {
  async function markLeave(repId: string, on: boolean) {
    await api("/api/leaves", {
      method: "POST",
      body: JSON.stringify({ rep_id: repId, day: data.day, on }),
    }).catch(() => undefined);
    refresh();
  }

  const t = data.team.today;
  const e = data.team.expected;
  const nowHour = data.is_today ? istanbulHour(data.now) : null;
  const own = me.role === "rep" ? data.reps[0] : undefined;
  const k = own ? own.today : t;
  const x = own ? own.expected : e;
  const tg = own ? own.target : data.team.target;
  const dolTarget = tg?.doluluk;

  return (
    <>
      {own ? (
        <div className="card card-body" style={{ display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
          <StatusPill status={own.status} />
          <span>
            Hareketsiz: <b>{minutes(own.idle_min)}</b>
          </span>
          <span className="muted">Ekip bugün {int(t.arama)} arama, {int(t.ulasilan)} ulaşılan</span>
        </div>
      ) : null}

      <div className="tiles">
        {countTile("Arama", "arama", k, x, tg)}
        {countTile("Ulaşılan", "ulasilan", k, x, tg)}
        {rateTile("Ulaşma oranı", k.ulasma_orani, tg?.ulasma_orani ? tg.ulasma_orani.daily : x.ulasma_orani, k.lead_payda, !!tg?.ulasma_orani)}
        {countTile("Toplantı", "randevu", k, x, tg)}
        <StatTile
          label="Ort. görüşme süresi"
          value={duration(k.sure_ort)}
          expected={tg?.sure_ort ? `hedef en az ${duration(tg.sure_ort.daily)}` : `ortalama ${duration(x.sure_ort)}`}
          tone={paceTone(pace(k.sure_ort, tg?.sure_ort ? tg.sure_ort.daily : x.sure_ort))}
          badge={
            pace(k.sure_ort, tg?.sure_ort ? tg.sure_ort.daily : x.sure_ort) === null
              ? undefined
              : `%${Math.round((pace(k.sure_ort, tg?.sure_ort ? tg.sure_ort.daily : x.sure_ort) ?? 0) * 100)}`
          }
          title="Ulaşılan giden görüşmelerin ortalama süresi. Hedef en az 5 dk; altına düşünce (en az 5 görüşmede) dürtme gider."
        />
        <StatTile
          label="Doluluk"
          value={pct(k.doluluk)}
          expected={`${minutes(k.kayip_dk)} kayıp zaman`}
          tone={paceTone(pace(k.doluluk, dolTarget ? dolTarget.daily : x.doluluk))}
          badge={
            dolTarget
              ? `${dolTarget.source === "ortalama" ? "ort." : "hedef"} ${pct(dolTarget.daily)}`
              : x.doluluk === null
                ? undefined
                : `ort. ${pct(x.doluluk)}`
          }
          title={`Hedef %80 (mola dışında boş zaman kalmamalı). Dolu ${minutes(k.dolu_dk)} / (dolu + kayıp ${minutes(k.kayip_dk)}). Arama 1 dk önce ve sonrasıyla, toplantı süresi (gelmeyen toplantıda 10 dk bekleme), WhatsApp konuşma başına 3 dk, CRM işlemi başına 2 dk. Günün en uzun boşluklarından ${minutes(k.mola_dk)} mola düşüldü.`}
        />
      </div>

      <CommissionCard isAdmin={me.role === "admin"} syncTick={syncTick} day={data.is_today ? null : data.day} />

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>Saat saat {own ? "benim" : "ekip"} performansı</h2>
            <span className="hint">çubuk bugün · çizgi aynı saatin ortalaması</span>
          </div>
          <div className="card-body">
            <HourChart hours={own ? own.hours : data.hours} />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Dikkat gerektirenler</h2>
            <span className="hint">şimdi</span>
          </div>
          <div className="card-body">
            <Attention data={data} />
          </div>
        </section>
      </div>

      {me.role === "admin" && data.is_today ? <NudgePanel /> : null}

      {me.role === "admin" ? <Commentary day={data.day} dataAsOf={data.data_as_of} /> : null}

      {data.meetings ? (
        <section className="card">
          <div className="card-head">
            <h2>Toplantılar</h2>
            <span className="hint">satıra tıkla: toplantı listesi</span>
          </div>
          <div className="card-body">
            <MeetingBoard meetings={data.meetings} reps={data.reps} />
          </div>
        </section>
      ) : null}

      {data.portfolio ? <PortfolioCard portfolio={data.portfolio} reps={data.reps} /> : null}

      <section className="card">
        <div className="card-head">
          <h2>Kayıp zaman</h2>
          <span className="hint">mesai şeridi · en uzun boşluklardan 1 sa 15 dk mola düşülür</span>
        </div>
        <div className="card-body">
          <LostTime reps={data.reps} day={data.day} now={data.is_today ? data.now : null} />
        </div>
      </section>

      {me.role === "admin" ? (
        <section className="card">
          <div className="card-head">
            <h2>Temsilciler</h2>
            <span className="hint">en acil durum üstte</span>
          </div>
          <div className="card-body">
            <RepBoard
              reps={data.reps}
              nowHour={nowHour}
              warnIdle={data.idle_thresholds.warn}
              crmTracking={data.crm_tracking}
              now={new Date(data.now)}
              onLeave={markLeave}
              meetings={data.meetings?.by_rep}
            />
          </div>
        </section>
      ) : null}
    </>
  );
}

function Freshness({ data, now, loading }: { data: LivePayload | null; now: Date; loading: boolean }) {
  if (!data) return null;
  if (!data.is_today) {
    return (
      <span className="freshness">
        <span className="dot off" /> Geçmiş gün
      </span>
    );
  }
  const ageMin = data.data_as_of ? (now.getTime() - new Date(data.data_as_of).getTime()) / 60000 : null;
  const stale = ageMin === null || ageMin > STALE_MIN;
  return (
    <span className="freshness" title="Son başarılı veri senkronu">
      <span className={`dot ${stale ? "stale" : "live"}`} />
      {loading ? "yenileniyor…" : `veri ${ago(data.data_as_of, now)}`}
    </span>
  );
}
