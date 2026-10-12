// Yalnız `npm run dev:mock`: veritabanı olmadan arayüz geliştirme. Üretime girmez.
import type { HourRow, Kpis, LivePayload, Me, RepCard, RepStatus } from "../src/types";

const REPS: [string, string, number][] = [
  ["r1", "Ayşe Yılmaz", 1.15],
  ["r2", "Mehmet Kaya", 0.62],
  ["r3", "Zeynep Demir", 0.95],
  ["r4", "Can Öztürk", 0.82],
  ["r5", "Elif Şahin", 0.0],
];

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

function kpis(arama: number, avail: number, r: () => number, nReps = 1): Kpis {
  const temas = Math.round(arama * (0.28 + r() * 0.1));
  const donus = Math.round(temas * 0.15);
  const payda = Math.round(arama * 0.8);
  const randevu = Math.round(temas * 0.25);
  const katildi = Math.round(randevu * 0.7);
  const sureOrt = 140 + r() * 80;
  const callSec = temas * sureOrt + (arama - temas) * 20;
  const busy = callSec / 60 + katildi * 30 + (arama - temas) * 0.5 + temas * 1.5 + arama / 3;
  const mesai = avail * nReps;
  return {
    arama,
    giden_temas: temas,
    donus,
    gelen: Math.round(donus / 2),
    ulasilan: temas + donus,
    lead_payda: payda,
    lead_pay: Math.round(payda * 0.36),
    ulasma_orani: payda ? Math.round((360 * (0.9 + r() * 0.2))) / 10 : null,
    randevu,
    katildi,
    katilmadi: randevu - katildi,
    sonuc_girilmedi: 0,
    katilim_orani: randevu ? Math.round((katildi / randevu) * 1000) / 10 : null,
    katilim_payda: randevu,
    whatsapp: Math.round(arama * 0.2),
    sure_toplam: temas * sureOrt,
    sure_ort: temas ? sureOrt : null,
    sure_tipik: temas ? sureOrt * 0.85 : null,
    call_sec: callSec,
    meet_dk: katildi * 30,
    dolu_dk: busy,
    mesai_dk: mesai,
    doluluk: mesai ? Math.min(100, Math.round((busy / mesai) * 1000) / 10) : null,
    n_reps: nReps,
  };
}

function sum(rows: Kpis[], avail: number, nReps: number): Kpis {
  const r = rng(7);
  const arama = rows.reduce((a, k) => a + k.arama, 0);
  const out = kpis(arama, avail, r, nReps);
  out.sonuc_girilmedi = 2;
  return out;
}

const iso = (d: Date) => d.toISOString();

function live(): LivePayload {
  const now = new Date();
  const local = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Istanbul" }));
  const nowHour = Math.min(Math.max(local.getHours(), 9), 17);
  const minute = local.getHours() >= 9 && local.getHours() < 18 ? local.getMinutes() : 59;
  const hours = Array.from({ length: 9 }, (_, i) => 9 + i);
  const at = (h: number, m: number) => new Date(now.getTime() - ((nowHour - h) * 60 + (minute - m)) * 60000);

  const cards: RepCard[] = REPS.map(([id, name, perf], idx) => {
    const r = rng(idx + 3);
    const hourRows: HourRow[] = hours.map((h) => {
      const done = h < nowHour;
      const base = h >= 11 && h < 14 ? 4 : 11;
      const frac = h < nowHour ? 1 : h === nowHour ? minute / 60 : 0;
      const arama = Math.round(base * perf * frac * (0.8 + r() * 0.4));
      return {
        hour: h,
        done,
        today: kpis(arama, 52 * frac, r),
        expected: kpis(base, 52, r),
      };
    });
    const arama = hourRows.reduce((a, h) => a + h.today.arama, 0);
    const expArama = hourRows.reduce(
      (a, h) => a + (h.hour < nowHour ? h.expected.arama : h.hour === nowHour ? (h.expected.arama * minute) / 60 : 0),
      0,
    );
    const avail = ((nowHour - 9) * 60 + minute) * (465 / 540);
    const idle = [4, 38, 17, 9, null][idx];
    const status: RepStatus = perf === 0 ? "baslamadi" : idle! >= 30 ? "hareketsiz" : idle! >= 15 ? "bosta" : "aktif";
    const last = idle === null ? null : iso(new Date(now.getTime() - idle * 60000));
    return {
      rep_id: id,
      name,
      today: kpis(arama, avail, r),
      expected: kpis(Math.round(expArama * 10) / 10, avail, r),
      first_at: perf === 0 ? null : iso(at(9, 4 + idx * 3)),
      last_active_at: perf === 0 ? null : last,
      last_call_at: perf === 0 ? null : last,
      last_meeting_at: null,
      last_whatsapp_at: null,
      last_crm_at: null,
      idle_min: perf === 0 ? ((nowHour - 9) * 60 + minute) : idle,
      status,
      hours: hourRows,
    };
  });

  const avail = ((nowHour - 9) * 60 + minute) * (465 / 540);
  const n = cards.length;
  const teamHours: HourRow[] = hours.map((h, i) => ({
    hour: h,
    done: h < nowHour,
    today: sum(cards.map((c) => c.hours[i].today), 52, n),
    expected: sum(cards.map((c) => c.hours[i].expected), 52, n),
  }));
  return {
    day: local.toISOString().slice(0, 10),
    now: iso(now),
    is_today: true,
    in_shift: true,
    data_as_of: iso(new Date(now.getTime() - 4 * 60000)),
    history_days: 64,
    team: {
      today: sum(cards.map((c) => c.today), avail, n),
      expected: sum(cards.map((c) => c.expected), avail, n),
    },
    hours: teamHours,
    reps: cards,
    idle_thresholds: { warn: 15, alert: 30 },
  };
}

const ME: Me = { email: "yusuf.aras@rexven.com", role: "admin", rep_id: null, full_name: "Yusuf Aras" };

export function mockApi(method: string, url: string): unknown {
  const path = url.split("?")[0];
  if (path === "/api/me") return ME;
  if (path === "/api/live") return live();
  if (path === "/api/login" && method === "POST") return { email: ME.email, role: ME.role };
  if (path === "/api/logout") return { ok: "true" };
  return undefined;
}
