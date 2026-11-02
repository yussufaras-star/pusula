const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const time = new Intl.DateTimeFormat("tr-TR", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Istanbul",
});
const longDay = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "long",
  weekday: "long",
  timeZone: "Europe/Istanbul",
});

export const num = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : nf.format(v);

export const int = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : nf0.format(v);

export const pct = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : `%${nf.format(v)}`;

export function duration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return "—";
  const s = Math.round(sec);
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r} sn`;
  return r ? `${m} dk ${r} sn` : `${m} dk`;
}

export function minutes(min: number | null | undefined): string {
  if (min === null || min === undefined) return "—";
  const m = Math.round(min);
  if (m < 60) return `${m} dk`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} sa ${m % 60} dk` : `${h} sa`;
}

export const clock = (iso: string | null | undefined): string =>
  iso ? time.format(new Date(iso)) : "—";

export const dayLabel = (isoDay: string): string =>
  longDay.format(new Date(`${isoDay}T12:00:00+03:00`));

export function ago(iso: string | null | undefined, now: Date): string {
  if (!iso) return "—";
  const min = (now.getTime() - new Date(iso).getTime()) / 60000;
  if (min < 1) return "az önce";
  return `${minutes(min)} önce`;
}

export const hourLabel = (h: number): string => `${String(h).padStart(2, "0")}:00`;

/** Pazartesi (YYYY-AA-GG) → "Q4W5". Hafta, perşembesinin düştüğü çeyreğe sayılır (ISO hafta
 *  kuralı): W1 çeyreğin ilk perşembesini içeren haftadır. Başka yılsa yıl eklenir ("Q4W5 '25"). */
export function weekCode(monday: string, thisYear?: number): string {
  const thu = new Date(`${monday.slice(0, 10)}T00:00:00Z`);
  thu.setUTCDate(thu.getUTCDate() + 3);
  const y = thu.getUTCFullYear();
  const q = Math.floor(thu.getUTCMonth() / 3);
  const days = (thu.getTime() - Date.UTC(y, q * 3, 1)) / 86_400_000;
  const code = `Q${q + 1}W${Math.floor(days / 7) + 1}`;
  return thisYear !== undefined && y !== thisYear ? `${code} '${String(y).slice(2)}` : code;
}

/** Gerçekleşen / beklenen. Beklenen yoksa null. */
export function pace(actual: number | null, expected: number | null): number | null {
  if (actual === null || expected === null || expected <= 0) return null;
  return actual / expected;
}

export type Tone = "good" | "warning" | "critical" | "neutral";

/** %80 kuralı: beklenenin %80 altı kritik, %80-100 uyarı. */
export function paceTone(ratio: number | null): Tone {
  if (ratio === null) return "neutral";
  if (ratio >= 1) return "good";
  if (ratio >= 0.8) return "warning";
  return "critical";
}

const tlFmt = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });

/** 1.225.478 TL — kuruş yok. */
export const tl = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : `${tlFmt.format(Math.round(v))} TL`;

/** Kısa: 1,2 Mn TL / 845 B TL. Eksen ve sıkışık hücreler için. */
export function tlShort(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (Math.abs(v) >= 1_000_000) return `${nf.format(Math.round(v / 100_000) / 10)} Mn TL`;
  if (Math.abs(v) >= 1_000) return `${nf0.format(Math.round(v / 1000))} B TL`;
  return `${nf0.format(v)} TL`;
}
