import { useState } from "react";

import { api, usePolling } from "../api";
import { Attention } from "../components/Attention";
import { HourChart } from "../components/HourChart";
import { LostTime } from "../components/LostTime";
import { RepBoard, StatusPill } from "../components/RepBoard";
import { StatTile } from "../components/StatTile";
import { SyncButton } from "../components/SyncButton";
import { ago, dayLabel, duration, int, minutes, num, pace, paceTone, pct, type Tone } from "../format";
import type { Kpis, LivePayload, Me } from "../types";

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

function countTile(label: string, key: keyof Kpis, t: Kpis, e: Kpis, unit?: string) {
  const actual = Number(t[key] ?? 0);
  const expected = Number(e[key] ?? 0);
  const ratio = pace(actual, expected);
  const tone = paceTone(ratio);
  return (
    <StatTile
      label={label}
      value={int(actual)}
      unit={unit}
      expected={`beklenen ${num(expected)}`}
      tone={tone}
      badge={ratio === null ? undefined : `%${Math.round(ratio * 100)}`}
    />
  );
}

function rateTile(label: string, actual: number | null, expected: number | null, sample: number) {
  const diff = actual !== null && expected !== null ? actual - expected : null;
  const tone: Tone =
    diff === null || sample < 5 ? "neutral" : diff >= 0 ? "good" : diff > -10 ? "warning" : "critical";
  return (
    <StatTile
      label={label}
      value={sample < 5 && actual !== null ? <span title="Örneklem 5'ten az">{pct(actual)}</span> : pct(actual)}
      expected={sample < 5 ? `örneklem ${sample} · az` : `ortalama ${pct(expected)}`}
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
            <SyncButton onDone={refresh} />
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
        <LiveBody data={data} me={me} refresh={refresh} />
      )}
    </>
  );
}

function LiveBody({ data, me, refresh }: { data: LivePayload; me: Me; refresh: () => void }) {
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
        {countTile("Arama", "arama", k, x)}
        {countTile("Ulaşılan", "ulasilan", k, x)}
        {rateTile("Ulaşma oranı", k.ulasma_orani, x.ulasma_orani, k.lead_payda)}
        {countTile("Randevu", "randevu", k, x)}
        <StatTile
          label="Ort. görüşme süresi"
          value={duration(k.sure_ort)}
          expected={`ortalama ${duration(x.sure_ort)}`}
          tone={paceTone(pace(k.sure_ort, x.sure_ort))}
          badge={pace(k.sure_ort, x.sure_ort) === null ? undefined : `%${Math.round((pace(k.sure_ort, x.sure_ort) ?? 0) * 100)}`}
        />
        <StatTile
          label="Doluluk"
          value={pct(k.doluluk)}
          expected={`${minutes(k.kayip_dk)} kayıp zaman`}
          tone={paceTone(pace(k.doluluk, x.doluluk))}
          badge={x.doluluk === null ? undefined : `ort. ${pct(x.doluluk)}`}
          title={`Dolu ${minutes(k.dolu_dk)} / (dolu + kayıp ${minutes(k.kayip_dk)}). Arama 1 dk önce ve sonrasıyla, toplantı süresi, WhatsApp mesaj başına 1 dk. Günün en uzun boşluklarından ${minutes(k.mola_dk)} mola düşüldü.`}
        />
      </div>

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
