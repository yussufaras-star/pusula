// Yalnız `npm run dev:mock`: veritabanı olmadan arayüz geliştirme. Üretime girmez.
import type {
  CompareSide,
  Gap,
  HourRow,
  Kpis,
  LivePayload,
  MeetingItem,
  Meetings,
  Me,
  RepCard,
  RepStatus,
  Segment,
} from "../src/types";

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
  const mola = Math.min(75 * nReps, Math.max(mesai - busy, 0) * 0.5);
  const kayip = Math.max(mesai - busy - mola, 0);
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
    dolu_dk: Math.min(busy, mesai),
    mola_dk: mola,
    kayip_dk: kayip,
    girilmemis_dk: 0,
    toplanti_dk: Math.round(busy * 0.2),
    mesai_dk: mesai,
    doluluk: mesai ? Math.round((Math.min(busy, mesai) / (Math.min(busy, mesai) + kayip)) * 1000) / 10 : null,
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

/** Örnek şerit: dolu bloklar, öğlen uzun boşluk (mola), aralarda kayıp. */
function timeline(perf: number, at: (h: number, m: number) => Date, nowHour: number, minute: number, r: () => number) {
  if (perf === 0) return { timeline: [], gaps: [] };
  const end = nowHour * 60 + minute;
  const segs: Segment[] = [];
  let t = 9 * 60;
  while (t < end) {
    const busy = Math.round(20 + r() * 60 * perf);
    const gap = t > 12 * 60 + 20 && t < 13 * 60 ? 60 : Math.round(r() * 25 * (1.4 - Math.min(perf, 1.2)));
    const b = Math.min(t + busy, end);
    segs.push({
      start: iso(at(Math.floor(t / 60), t % 60)),
      end: iso(at(Math.floor(b / 60), b % 60)),
      kind: busy > 50 ? "toplanti" : "dolu",
    });
    const g = Math.min(b + gap, end);
    if (g > b) {
      segs.push({
        start: iso(at(Math.floor(b / 60), b % 60)),
        end: iso(at(Math.floor(g / 60), g % 60)),
        kind: gap >= 40 ? "mola" : gap >= 18 ? "girilmemis" : "kayip",
      });
    }
    t = g;
  }
  const gaps: Gap[] = segs
    .filter((s) => s.kind !== "dolu" && s.kind !== "toplanti")
    .map((s) => ({ ...s, minutes: (new Date(s.end).getTime() - new Date(s.start).getTime()) / 60000 }))
    .filter((g) => g.minutes >= 5)
    .sort((a, b) => b.minutes - a.minutes)
    .slice(0, 5);
  return { timeline: segs, gaps };
}

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
      last_crm_at: perf === 0 || idx === 1 ? null : iso(new Date(now.getTime() - (idx + 2) * 7 * 60000)),
      idle_min: perf === 0 ? ((nowHour - 9) * 60 + minute) : idle,
      status: LEAVES.has(id) ? "izinli" : idx === 2 ? "toplantida" : status,
      on_leave: LEAVES.has(id),
      hours: hourRows,
      ...timeline(perf, at, nowHour, minute, r),
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
    crm_tracking: true,
    history_days: 64,
    team: {
      today: sum(cards.map((c) => c.today), avail, n),
      expected: sum(cards.map((c) => c.expected), avail, n),
    },
    hours: teamHours,
    reps: cards,
    idle_thresholds: { warn: 15, alert: 30 },
    meetings: mockMeetings(cards.map((c) => c.rep_id), now),
  };
}

const NAMES: Record<string, string> = {
  team: "Ekip toplamı",
  avg: "Temsilci ortalaması",
  ...Object.fromEntries(REPS.map(([id, name]) => [id, name])),
};

function compareSide(q: URLSearchParams, p: "a" | "b"): CompareSide {
  const first = q.get(`${p}_from`) ?? "";
  const last = q.get(`${p}_to`) ?? first;
  const subject = q.get(`${p}_subject`) ?? "team";
  const perf = REPS.find(([id]) => id === subject)?.[2] ?? 1;
  const n = subject === "team" ? REPS.length : 1;
  const seed = [...first + subject].reduce((a, c) => a + c.charCodeAt(0), 0);
  const r = rng(seed);
  const oneDay = q.get("a_from") === q.get("a_to") && q.get("b_from") === q.get("b_to");
  const buckets: (number | string)[] = [];
  if (oneDay) for (let h = 9; h < 18; h++) buckets.push(h);
  else {
    const d = new Date(`${first}T12:00:00Z`);
    const end = new Date(`${last}T12:00:00Z`);
    while (d <= end) {
      if (d.getUTCDay() !== 0) buckets.push(d.toISOString().slice(0, 10));
      d.setUTCDate(d.getUTCDate() + 1);
    }
  }
  const unit = (oneDay ? 10 : 85) * n * Math.max(perf, 0.3);
  const series = buckets.map((b) => ({
    bucket: b,
    partial: false,
    kpis: kpis(Math.round(unit * (0.7 + r() * 0.6)), (oneDay ? 52 : 465) * n, r, n),
  }));
  const arama = series.reduce((a, s) => a + s.kpis.arama, 0);
  const days = oneDay ? 1 : buckets.length;
  return {
    label: NAMES[subject] ?? subject,
    subject,
    first,
    last,
    cut: `${last}T18:00:00+03:00`,
    aligned: p === "b" && q.get("align") !== "false",
    work_days: days,
    n_reps: n,
    kpis: kpis(arama, 465 * days, r, n),
    series_group: oneDay ? "hour" : "day",
    series,
  };
}

function mockMeetings(repIds: string[], now: Date): Meetings {
  const states: MeetingItem["state"][] = ["geldi", "gelmedi", "geldi", "bilinmiyor", "gelmedi", "suruyor", "bekleniyor"];
  const names = ["Hakan Düz", "İlayda Ünaydın", "Seher Kaya", "Hilal Er", "Onur Tan", "Okan Tıraş", "Adem Kara"];
  const items: MeetingItem[] = [];
  repIds.slice(0, 4).forEach((rep, ri) => {
    for (let i = 0; i < 4 + ri; i++) {
      const state = states[(i + ri) % states.length];
      const start = new Date(now.getTime() - (5 - i) * 50 * 60000);
      items.push({
        rep_id: rep,
        start: start.toISOString(),
        planned_min: 30,
        customer: names[(i + ri * 2) % names.length],
        state,
        source: i % 3 ? "zoho_meeting" : "crm",
        crm_status: state === "geldi" ? "katildi" : state === "gelmedi" && i % 2 ? "katilmadi" : "sonuc_girilmedi",
        result_entered: state === "geldi" || (state === "gelmedi" && i % 2 === 1),
        mismatch: ri === 1 && i === 1,
        minutes: state === "geldi" ? 25 + i * 4 : state === "gelmedi" ? 10 : 0,
        call_after: state === "gelmedi" && i % 2 ? new Date(start.getTime() + 18 * 60000).toISOString() : null,
        wa_after: null,
        followed_up: state === "gelmedi" && i % 2 === 1,
      });
    }
  });
  const by_rep: Meetings["by_rep"] = {};
  for (const m of items) {
    const s = (by_rep[m.rep_id] ??= {
      planlanan: 0, gelen: 0, gelmeyen: 0, bilinmiyor: 0, bekleyen: 0, iptal: 0, sonuc_girilen: 0,
      sonuc_girilmemis: 0, toplam_dk: 0, aranan: 0, aranmayan: 0, uyumsuz: 0, katilim_orani: null,
    });
    s.planlanan++;
    s.toplam_dk += m.minutes;
    if (m.state === "geldi") s.gelen++;
    else if (m.state === "gelmedi") { s.gelmeyen++; m.followed_up ? s.aranan++ : s.aranmayan++; }
    else if (m.state === "bilinmiyor") s.bilinmiyor++;
    else s.bekleyen++;
    if (!["bekleniyor", "suruyor"].includes(m.state)) m.result_entered ? s.sonuc_girilen++ : s.sonuc_girilmemis++;
    if (m.mismatch) s.uyumsuz++;
  }
  for (const s of Object.values(by_rep)) {
    const d = s.gelen + s.gelmeyen;
    s.katilim_orani = d ? Math.round((1000 * s.gelen) / d) / 10 : null;
  }
  return { items, by_rep };
}

const LEAVES = new Set<string>(["r5"]);

const ME: Me = { email: "yusuf.aras@rexven.com", role: "admin", rep_id: null, full_name: "Yusuf Aras" };

export function mockApi(method: string, url: string): unknown {
  const path = url.split("?")[0];
  if (path === "/api/me") return ME;
  if (path === "/api/live") return live();
  if (path === "/api/leaves" && method === "POST") {
    LEAVES.has("r5") ? LEAVES.delete("r5") : LEAVES.add("r5");
    return { ok: true };
  }
  if (path === "/api/reps") return REPS.map(([rep_id, full_name]) => ({ rep_id, full_name }));
  if (path === "/api/compare") {
    const q = new URLSearchParams(url.split("?")[1] ?? "");
    return { now: new Date().toISOString(), a: compareSide(q, "a"), b: compareSide(q, "b") };
  }
  if (path === "/api/login" && method === "POST") return { email: ME.email, role: ME.role };
  if (path === "/api/logout") return { ok: "true" };
  if (path === "/api/ingest" && method === "POST") {
    return {
      seconds: 14.2,
      steps: {
        aramalar: { fetched: 9, inserted: 7, failed: 0 },
        whatsapp: { fetched: 3, inserted: 2, failed: 0 },
        crm_islemleri: { Notes: { fetched: 4, written: 3 }, Leads: { fetched: 6, written: 5 } },
      },
    };
  }
  return undefined;
}
