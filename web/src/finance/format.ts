import type { CompanyId, Scope } from "./types";

// Şirket renkleri sabittir: hangi şirketler görünürse görünsün aynı kalır.
// Değerler styles.css'teki --co-llc / --co-gmbh / --co-tr (iki tema doğrulandı).
export const COMPANY_IDS: CompanyId[] = ["llc", "gmbh", "tr"];
export const COMPANY_SHORT: Record<CompanyId, string> = { llc: "LLC", gmbh: "GmbH", tr: "TR" };
export const companyColor = (id: CompanyId): string => `var(--co-${id})`;

export const SCOPES: { key: Scope; label: string }[] = [
  { key: "all", label: "Konsolide" },
  { key: "llc", label: "LLC" },
  { key: "gmbh", label: "GmbH" },
  { key: "tr", label: "TR" },
];

const SYMBOL: Record<string, string> = { USD: "$", EUR: "€", GBP: "£", TRY: "₺" };

function grouped(v: number, digits: number): string {
  return new Intl.NumberFormat("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v);
}

/** $12.400 / −€1.234,50 */
export function money(v: number | null | undefined, currency = "USD", digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sym = SYMBOL[currency] ?? `${currency} `;
  const sign = v < 0 ? "−" : "";
  return `${sign}${sym}${grouped(Math.abs(v), digits)}`;
}

export const usd = (v: number | null | undefined): string => money(v, "USD");

/** +$1.200 / −$300 */
export function signed(v: number | null | undefined, currency = "USD"): string {
  if (v === null || v === undefined) return "—";
  return v > 0 ? `+${money(v, currency)}` : money(v, currency);
}

/** Kısa: $1,2 Mn / $845 B (B = bin). Eksen ve kutular için. */
export function short(v: number | null | undefined, currency = "USD"): string {
  if (v === null || v === undefined) return "—";
  const sym = SYMBOL[currency] ?? `${currency} `;
  const a = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  const nf = (x: number) => new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 }).format(x);
  if (a >= 1_000_000) return `${sign}${sym}${nf(a / 1_000_000)} Mn`;
  if (a >= 1_000) return `${sign}${sym}${nf(a / 1_000)} B`;
  return `${sign}${sym}${Math.round(a)}`;
}

const dayFmt = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const dayShort = new Intl.DateTimeFormat("tr-TR", { day: "numeric", month: "short", timeZone: "UTC" });
const monthFmt = new Intl.DateTimeFormat("tr-TR", { month: "short", year: "2-digit", timeZone: "UTC" });
const stamp = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Istanbul",
});

export const day = (iso: string | null | undefined): string => (iso ? dayFmt.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`)) : "—");
export const dayS = (iso: string): string => dayShort.format(new Date(`${iso.slice(0, 10)}T00:00:00Z`));
export const month = (ym: string): string => monthFmt.format(new Date(`${ym}-01T00:00:00Z`));
export const when = (iso: string | null | undefined): string => (iso ? stamp.format(new Date(iso)) : "—");

export function istanbulToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** Haftanın pazartesisi. */
export function monday(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return addDays(iso, -((d.getUTCDay() + 6) % 7));
}

export function niceMax(v: number): number {
  if (v <= 0) return 1;
  const mag = 10 ** Math.floor(Math.log10(v));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => v / s <= 4) ?? mag * 10;
  return Math.ceil(v / step) * step;
}

/** Eksen: 0'ı içeren, yuvarlak adımlı (1-2-2,5-5 × 10ⁿ) en fazla ~5 aralık. */
export function niceScale(lo: number, hi: number): { lo: number; hi: number; ticks: number[] } {
  const a = Math.min(lo, 0);
  const b = Math.max(hi, 0);
  const span = b - a || 1;
  const mag = 10 ** Math.floor(Math.log10(span / 4));
  const step = [1, 2, 2.5, 5, 10].map((x) => x * mag).find((x) => span / x <= 5) ?? mag * 10;
  const first = Math.floor(a / step) * step;
  const last = Math.ceil(b / step) * step || step;
  const ticks: number[] = [];
  for (let t = first; t <= last + step / 2; t += step) ticks.push(Math.round(t / step) * step);
  return { lo: first, hi: last, ticks };
}

/** Dosya → base64 (ekstre yükleme). */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
