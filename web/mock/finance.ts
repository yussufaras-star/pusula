// Yalnız `npm run dev:mock`: finans ekranları için örnek veri. Üretime girmez.
// Hesaplar sunucudaki pusula/finance/views.py'nin sadeleştirilmiş karşılığıdır.
import type {
  Account,
  Category,
  Company,
  CompanyId,
  Decision,
  PlannedItem,
  SnapshotCard,
  SnapshotLine,
  Tx,
} from "../src/finance/types";

const RATE: Record<string, number> = { USD: 1, EUR: 1.1208, GBP: 1.3247, TRY: 0.020236 };
const FX = { fx_date: "2026-10-15", fx: { EUR: 1.1208, GBP: 1.3247, TRY: 0.020236 }, missing_fx: [] as string[] };
const toUsd = (amount: number, cur: string) => amount * (RATE[cur] ?? 0);
const r2 = (v: number) => Math.round(v * 100) / 100;

const COMPANIES: Company[] = [
  { id: "llc", name: "Rexven LLC", country: "US", base_currency: "USD", match_patterns: ["%rexven llc%"], sort: 1 },
  { id: "gmbh", name: "Rexven GmbH", country: "DE", base_currency: "EUR", match_patterns: ["%rexven gmbh%"], sort: 2 },
  { id: "tr", name: "Rexven TR", country: "TR", base_currency: "TRY", match_patterns: ["%rexven%teknoloji%"], sort: 3 },
];

const CATEGORIES: Category[] = [
  ["satis", "Satış / Platform geliri", "in", 1],
  ["proje", "Proje geliri", "in", 2],
  ["diger_gir", "Diğer giriş", "in", 3],
  ["tedarikci", "Tedarikçi", "out", 10],
  ["kargo", "Kargo / Lojistik", "out", 11],
  ["maas", "Maaş + SGK + Vergi", "out", 12],
  ["vergi", "Vergi / KDV", "out", 13],
  ["reklam", "Reklam / Pazarlama", "out", 14],
  ["yazilim", "Yazılım / Abonelik", "out", 15],
  ["danismanlik", "Danışmanlık / Hukuk", "out", 16],
  ["banka", "Banka / Komisyon", "out", 17],
  ["diger_cik", "Diğer çıkış", "out", 18],
  ["ic_transfer", "Şirketler arası transfer", "transfer", 20],
  ["hesap_arasi", "Kendi hesapları arası", "transfer", 21],
].map(([id, name, kind, sort]) => ({ id, name, kind, sort }) as Category);
const KIND = Object.fromEntries(CATEGORIES.map((c) => [c.id, c.kind]));

const now = new Date();
const iso = (d: Date) => d.toISOString().slice(0, 10);
const ago = (h: number) => new Date(now.getTime() - h * 3600_000).toISOString();
const addDays = (s: string, n: number) => {
  const d = new Date(`${s}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return iso(d);
};
const TODAY = iso(now);
const monday = (s: string) => addDays(s, -((new Date(`${s}T00:00:00Z`).getUTCDay() + 6) % 7));

const ACCOUNTS: Account[] = [
  acc(1, "llc", "Mercury Ops", "Mercury", "fintech", "USD", "mercury", "LLC", 184_250, ago(5)),
  acc(2, "llc", "Stripe", "Stripe", "payment", "USD", "stripe", "LLC", 23_480, ago(5)),
  acc(3, "gmbh", "Wise EUR", "Wise", "fintech", "EUR", "wise", "GMBH", 61_900, ago(5), null, "Wise: hareketler için SCA gerekli, yalnız bakiye güncellendi"),
  acc(4, "gmbh", "Revolut EUR", "Revolut", "fintech", "EUR", "revolut", "GMBH", 12_340, ago(29), "Revolut: HTTP 401 – invalid refresh token"),
  acc(5, "tr", "Garanti TL", "Garanti BBVA", "bank", "TRY", "csv", null, 2_940_000, ago(30)),
  acc(6, "tr", "KuveytTürk TL", "KuveytTürk", "bank", "TRY", "csv", null, 615_000, ago(24 * 9)),
  acc(7, "tr", "Bonus kurumsal", "Garanti BBVA", "card", "TRY", "manual", null, -184_000, ago(24 * 3)),
];

function acc(
  id: number, company_id: CompanyId, name: string, institution: string, type: Account["type"], currency: string,
  provider: Account["provider"], credential_key: string | null, balance: number, synced: string,
  sync_error: string | null = null, sync_warning: string | null = null,
): Account {
  return {
    id, company_id, name, institution, type, currency, provider, credential_key,
    provider_account_id: provider === "wise" ? "123:456" : provider === "csv" || provider === "manual" ? null : `acc-${id}`,
    balance, balance_usd: r2(toUsd(balance, currency)), balance_at: synced, last_synced_at: synced,
    sync_error, sync_warning, active: true, sort: id,
  };
}

function rng(seed: number) {
  let s = seed;
  return () => {
    s = (s * 16807) % 2147483647;
    return s / 2147483647;
  };
}

// 12 aylık hareketler: şirket başına satış, maaş, kargo vb. + şirketler arası transferler.
const TXS: Tx[] = (() => {
  const r = rng(42);
  const out: Tx[] = [];
  let id = 1;
  const push = (account_id: number, booked_at: string, amount: number, description: string, category_id: string | null, cp: CompanyId | null = null, counterparty: string | null = null) => {
    const a = ACCOUNTS.find((x) => x.id === account_id)!;
    out.push({
      id: id++, account_id, account: a.name, company_id: a.company_id, booked_at, amount: r2(amount), currency: a.currency,
      amount_usd: r2(toUsd(amount, a.currency)), description, counterparty, category_id, counterparty_company_id: cp,
      source: a.provider === "csv" ? "csv" : "api", edited: false,
    });
  };
  for (let d = 365; d >= 0; d--) {
    const day = addDays(TODAY, -d);
    const dow = new Date(`${day}T00:00:00Z`).getUTCDay();
    if (dow === 0 || dow === 6) continue;
    const dom = Number(day.slice(8));
    push(2, day, 1800 + r() * 2600, "Stripe payout", "satis");
    if (r() < 0.5) push(1, day, -(120 + r() * 900), r() < 0.5 ? "AWS" : "Google Workspace", "yazilim");
    if (r() < 0.35) push(3, day, 900 + r() * 3200, "Amazon EU payout", "satis");
    if (r() < 0.25) push(3, day, -(200 + r() * 1400), "DHL Express", "kargo");
    push(5, day, 38_000 + r() * 70_000, "Trendyol hakediş", "satis");
    if (r() < 0.6) push(5, day, -(8_000 + r() * 30_000), "Aras Kargo", "kargo");
    if (r() < 0.3) push(6, day, -(4_000 + r() * 12_000), "POS komisyon", r() < 0.7 ? "banka" : null);
    if (dom <= 2 && dow !== 0) {
      push(5, day, -(1_150_000 + r() * 80_000), "Maaş ödemesi", "maas");
      push(1, day, -(14_000 + r() * 2_000), "Gusto payroll", "maas");
      push(3, day, -(9_500 + r() * 900), "Gehalt", "maas");
    }
    if (dom === 15) {
      push(1, day, -25_000, "Wire to REXVEN TEKNOLOJI A.S.", "ic_transfer", "tr", "Rexven Teknoloji A.Ş.");
      // Gecikmeli ve kur farklı karşılık; son ay karşılıksız kalır (yoldaki para)
      if (d > 20) push(5, addDays(day, 2), 25_000 / RATE.TRY * 0.992, "SWIFT GELEN REXVEN LLC", "ic_transfer", "llc", "Rexven LLC");
    }
    if (dom === 20 && r() < 0.6) {
      push(3, day, -8_000, "To Rexven LLC", "ic_transfer", "llc");
      push(1, addDays(day, 1), 8_000 * RATE.EUR * 0.995, "Wise transfer REXVEN GMBH", "ic_transfer", "gmbh");
    }
  }
  return out;
})();

const PLANNED: PlannedItem[] = [
  planned(1, "tr", "out", "SGK + muhtasar", 640_000, "TRY", addDays(TODAY, 10), "monthly", null, 1),
  planned(2, "tr", "out", "Ofis kirası", 185_000, "TRY", addDays(TODAY, 15), "monthly", null, 1),
  planned(3, "llc", "out", "Federal vergi", 48_000, "USD", addDays(TODAY, 35), "none", null, 1),
  planned(4, "llc", "in", "Kurumsal proje ödemesi", 60_000, "USD", addDays(TODAY, 21), "none", null, 0.6),
  planned(5, "gmbh", "out", "Umsatzsteuer", 14_500, "EUR", addDays(TODAY, 24), "monthly", null, 1),
  planned(6, "gmbh", "in", "Amazon EU rezerv çözülmesi", 22_000, "EUR", addDays(TODAY, 42), "none", null, 0.8),
  planned(7, "tr", "out", "Tedarikçi: ambalaj", 420_000, "TRY", addDays(TODAY, -6), "none", null, 1),
  planned(8, "llc", "out", "Google Ads", 3_500, "USD", addDays(TODAY, 3), "weekly", addDays(TODAY, 120), 1),
  planned(9, "llc", "out", "Yıllık yazılım lisansları", 210_000, "USD", addDays(TODAY, 70), "none", null, 1),
];

function planned(
  id: number, company_id: CompanyId, direction: "in" | "out", title: string, amount: number, currency: string, due_date: string,
  recurrence: PlannedItem["recurrence"], recur_until: string | null, probability: number,
): PlannedItem {
  return {
    id, company_id, direction, title, counterparty: null, amount, currency, due_date, recurrence, recur_until, probability,
    status: "open", category_id: null, notes: null, amount_usd: r2(toUsd(amount, currency)),
    overdue: recurrence === "none" && due_date < TODAY,
  };
}

const DECISIONS: Decision[] = [
  dec(1, null, "Stripe tahsilatı GmbH'ye taşınsın", "AB müşterileri için KDV ve kur maliyeti düşer.", "Yusuf", "aksiyon", "yuksek", addDays(TODAY, -3), 18_000),
  dec(2, "tr", "Kargo sözleşmesi yeniden fiyatlansın", "Aras ve Yurtiçi teklifleri karşılaştırılacak.", "Burcu", "devam", "orta", addDays(TODAY, 14), 42_000),
  dec(3, "llc", "Mercury'de atıl nakit hazine fonuna", "", "Yusuf", "acik", "orta", addDays(TODAY, 30), 6_500),
  dec(4, "gmbh", "Revolut hesabı kapatılsın", "Wise yeterli; aylık ücret ve bakım yükü.", null, "acik", "dusuk", null, 1_200),
  dec(5, null, "Haftalık finans toplantısı pazartesi 10:00", "", "Yusuf", "tamam", "orta", addDays(TODAY, -20), null),
];

function dec(
  id: number, company_id: CompanyId | null, title: string, description: string, owner: string | null,
  status: Decision["status"], priority: Decision["priority"], due_date: string | null, impact_usd: number | null,
): Decision {
  return { id, company_id, title, description, owner, status, priority, due_date, impact_usd, meeting_date: null, created_at: ago(500), updated_at: ago(30), overdue: false };
}

// --- yardımcılar --------------------------------------------------------------------

function scopeOf(q: URLSearchParams): CompanyId[] {
  const c = q.get("company") ?? "all";
  return c === "all" ? ["llc", "gmbh", "tr"] : [c as CompanyId];
}

function range(q: URLSearchParams): [string, string] {
  return [q.get("start") ?? addDays(TODAY, -182), q.get("end") ?? TODAY];
}

const isTransfer = (t: Tx) => !!t.counterparty_company_id || KIND[t.category_id ?? ""] === "transfer";
const cash = (ids: CompanyId[]) => ACCOUNTS.filter((a) => a.active && ids.includes(a.company_id)).reduce((s, a) => s + toUsd(a.balance, a.currency), 0);

function expand(items: PlannedItem[], start: string, end: string) {
  const out: { item: PlannedItem; day: string; overdue: boolean }[] = [];
  for (const it of items) {
    if (it.status !== "open") continue;
    if (it.recurrence === "none") {
      if (it.due_date <= end) out.push({ item: it, day: it.due_date < start ? start : it.due_date, overdue: it.due_date < start });
      continue;
    }
    const until = it.recur_until && it.recur_until < end ? it.recur_until : end;
    const dom = Number(it.due_date.slice(8));
    for (let d = it.due_date, n = 0; d <= until && n < 600; n++) {
      if (d >= start) out.push({ item: it, day: d, overdue: false });
      if (it.recurrence === "weekly") d = addDays(d, 7);
      else {
        const x = new Date(`${d}T00:00:00Z`);
        const y = x.getUTCMonth() === 11 ? x.getUTCFullYear() + 1 : x.getUTCFullYear();
        const m = (x.getUTCMonth() + 1) % 12;
        const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
        d = iso(new Date(Date.UTC(y, m, Math.min(dom, last))));
      }
    }
  }
  return out;
}

function projection(ids: CompanyId[], weeks: number, weighted: boolean) {
  const first = monday(TODAY);
  const occ = expand(PLANNED.filter((p) => ids.includes(p.company_id)), TODAY, addDays(first, weeks * 7 - 1));
  let bal = cash(ids);
  return Array.from({ length: weeks }, (_, i) => {
    const ws = addDays(first, 7 * i);
    const we = addDays(ws, 6);
    let inn = 0;
    let out = 0;
    const items = occ
      .filter((o) => o.day >= ws && o.day <= we)
      .map((o) => {
        const v = toUsd(o.item.amount, o.item.currency) * (weighted ? o.item.probability : 1);
        if (o.item.direction === "in") inn += v;
        else out += v;
        return { item_id: o.item.id, title: o.item.title, company_id: o.item.company_id, day: o.day, usd: r2(o.item.direction === "in" ? v : -v), overdue: o.overdue };
      });
    bal += inn - out;
    return { week: ws, in: r2(inn), out: r2(out), close: r2(bal), items };
  });
}

function months(txs: Tx[]) {
  const m = new Map<string, { month: string; in: number; out: number; net: number }>();
  for (const t of txs) {
    if (isTransfer(t)) continue;
    const k = t.booked_at.slice(0, 7);
    const row = m.get(k) ?? { month: k, in: 0, out: 0, net: 0 };
    if (t.amount_usd >= 0) row.in += t.amount_usd;
    else row.out -= t.amount_usd;
    row.net += t.amount_usd;
    m.set(k, row);
  }
  return [...m.values()].sort((a, b) => a.month.localeCompare(b.month)).map((x) => ({ month: x.month, in: r2(x.in), out: r2(x.out), net: r2(x.net) }));
}

function history(ids: CompanyId[], start: string) {
  const points: string[] = [];
  for (let d = monday(start); d < TODAY; d = addDays(d, 7)) points.push(d);
  points.push(TODAY);
  return points.map((day) => {
    const row: Record<string, number | string> = { day };
    let total = 0;
    for (const c of ids) {
      let v = 0;
      for (const a of ACCOUNTS.filter((x) => x.active && x.company_id === c)) {
        const after = TXS.filter((t) => t.account_id === a.id && t.booked_at > day).reduce((s, t) => s + t.amount, 0);
        v += toUsd(a.balance - after, a.currency);
      }
      row[c] = r2(v);
      total += v;
    }
    row.total = r2(total);
    return row;
  });
}

function unmatched(ic: Tx[]) {
  const outs = ic.filter((t) => t.amount < 0);
  const ins = ic.filter((t) => t.amount > 0);
  const used = new Set<number>();
  const lonelyOut = outs.filter((o) => {
    const usd = -o.amount_usd;
    const hit = ins.find(
      (i) =>
        !used.has(i.id) && i.company_id === o.counterparty_company_id && i.counterparty_company_id === o.company_id &&
        Math.abs(i.amount_usd - usd) <= usd * 0.02 &&
        Math.abs(new Date(i.booked_at).getTime() - new Date(o.booked_at).getTime()) <= 5 * 864e5,
    );
    if (hit) used.add(hit.id);
    return !hit;
  });
  return { outgoing: lonelyOut, incoming: ins.filter((i) => !used.has(i.id)) };
}

// --- bilanço ---------------------------------------------------------------------------

const GROUP: Record<Account["type"], string> = {
  bank: "Banka hesapları", fintech: "Fintech", payment: "Ödeme altyapıları", card: "Kredi kartları", shipping: "Kargo bakiyeleri", other: "Diğer",
};

function snapshot(id: number, company_id: CompanyId, week: string, locked: boolean, scale: number): SnapshotCard {
  const lines: SnapshotLine[] = [];
  const push = (side: SnapshotLine["side"], group_name: string, label: string, amount: number, currency: string, origin: SnapshotLine["origin"]) =>
    lines.push({ id: id * 100 + lines.length, snapshot_id: id, side, group_name, label, amount: r2(amount), currency, origin, sort: lines.length });
  for (const a of ACCOUNTS.filter((x) => x.company_id === company_id)) {
    if (a.balance < 0) push("liability", "Kredi kartları / Krediler", a.name, -a.balance * scale, a.currency, "account");
    else push("asset", GROUP[a.type], a.name, a.balance * scale, a.currency, "account");
  }
  for (const p of PLANNED.filter((x) => x.company_id === company_id && x.recurrence === "none" && x.status === "open")) {
    push(p.direction === "in" ? "asset" : "liability", p.direction === "in" ? "Vadeli alacaklar" : "Vadeli borçlar", p.title, p.amount, p.currency, "planned");
  }
  if (company_id === "tr") push("liability", "Tedarikçiler", "Ambalaj tedarikçisi", 260_000, "TRY", "manual");
  if (company_id === "llc") push("liability", "Tedarikçiler", "Ajans faturası", 12_000, "USD", "manual");
  const fx = { EUR: RATE.EUR, GBP: RATE.GBP, TRY: RATE.TRY };
  const usdOf = (l: SnapshotLine) => l.amount * (l.currency === "USD" ? 1 : fx[l.currency as keyof typeof fx]);
  const groups: SnapshotCard["groups"] = [];
  for (const l of lines) {
    const g = groups.find((x) => x.side === l.side && x.name === l.group_name);
    if (g) g.usd += usdOf(l);
    else groups.push({ side: l.side, name: l.group_name, usd: usdOf(l) });
  }
  groups.sort((a, b) => (a.side === b.side ? 0 : a.side === "asset" ? -1 : 1));
  const assets = lines.filter((l) => l.side === "asset").reduce((s, l) => s + usdOf(l), 0);
  const liabilities = lines.filter((l) => l.side === "liability").reduce((s, l) => s + usdOf(l), 0);
  return {
    id, company_id, week_date: week, locked, locked_at: locked ? ago(24 * 6) : null, notes: company_id === "tr" ? "SGK ödemesi bu hafta." : "", fx,
    assets_usd: r2(assets), liabilities_usd: r2(liabilities), net_usd: r2(assets - liabilities), missing_fx: [],
    groups: groups.map((g) => ({ ...g, usd: r2(g.usd) })), lines,
  };
}

const THIS_WEEK = monday(TODAY);
const LAST_WEEK = addDays(THIS_WEEK, -7);
const SNAPSHOTS: SnapshotCard[] = [
  snapshot(1, "llc", LAST_WEEK, true, 0.94),
  snapshot(2, "gmbh", LAST_WEEK, true, 0.97),
  snapshot(3, "tr", LAST_WEEK, true, 1.08),
  snapshot(4, "llc", THIS_WEEK, false, 1),
  snapshot(5, "gmbh", THIS_WEEK, false, 1),
  snapshot(6, "tr", THIS_WEEK, false, 1),
];

// --- uçlar -----------------------------------------------------------------------------

export function mockFinance(method: string, url: string): unknown {
  const [path, query = ""] = url.split("?");
  const q = new URLSearchParams(query);
  const ids = scopeOf(q);

  if (method !== "GET") return mockWrite(method, path);

  if (path === "/api/finance/meta") return { companies: COMPANIES, categories: CATEGORIES, ...FX };
  if (path === "/api/finance/accounts") return { accounts: ACCOUNTS.filter((a) => ids.includes(a.company_id)), ...FX };

  if (path === "/api/finance/overview") {
    const [start] = range(q);
    const hist = history(ids, start);
    const total = r2(cash(ids));
    const proj = projection(ids, 13, true);
    const low = proj.reduce((m, w) => (w.close < m.close ? w : m), proj[0]);
    const accounts = ACCOUNTS.filter((a) => ids.includes(a.company_id));
    const overdue = PLANNED.filter((p) => ids.includes(p.company_id) && p.overdue);
    const gap = proj.find((w) => w.close < 0) ?? null;
    return {
      as_of: new Date().toISOString(),
      cash_usd: total,
      start_cash_usd: Number(hist[0].total),
      change_usd: r2(total - Number(hist[0].total)),
      net_flow_usd: r2(months(TXS.filter((t) => ids.includes(t.company_id) && t.booked_at >= start)).reduce((s, m) => s + m.net, 0)),
      companies: COMPANIES.filter((c) => ids.includes(c.id)).map((c) => {
        const usd = cash([c.id]);
        return { id: c.id, name: c.name, currency: c.base_currency, cash_usd: r2(usd), cash_local: r2(usd / RATE[c.base_currency]), accounts: accounts.filter((a) => a.company_id === c.id).length };
      }),
      history: hist,
      projection: proj.map((w) => ({ week: w.week, close: w.close })),
      lowest: { week: low.week, close: low.close },
      alerts: {
        cash_gap: gap ? { week: gap.week, close: gap.close } : null,
        overdue: { count: overdue.length, usd: r2(overdue.reduce((s, p) => s + p.amount_usd, 0)) },
        sync_errors: accounts.filter((a) => a.sync_error).map((a) => ({ id: a.id, name: a.name, error: a.sync_error })),
        stale: accounts.filter((a) => !a.sync_error && a.provider === "csv" && a.last_synced_at! < ago(24 * 7)).map((a) => ({ id: a.id, name: a.name, last_synced_at: a.last_synced_at })),
        warnings: accounts.filter((a) => a.sync_warning).map((a) => ({ id: a.id, name: a.name, warning: a.sync_warning })),
      },
      open_actions: DECISIONS.filter((d) => d.status === "aksiyon" && (!d.company_id || ids.includes(d.company_id))).length,
      ...FX,
    };
  }

  if (path === "/api/finance/flows") {
    const [start, end] = range(q);
    const txs = TXS.filter((t) => ids.includes(t.company_id) && t.booked_at >= start && t.booked_at <= end);
    const m = months(txs);
    const cats = new Map<string, { id: string | null; name: string; in: number; out: number; count: number }>();
    for (const t of txs) {
      if (isTransfer(t)) continue;
      const k = t.category_id ?? "_none";
      const row = cats.get(k) ?? { id: t.category_id, name: CATEGORIES.find((c) => c.id === t.category_id)?.name ?? "Kategorisiz", in: 0, out: 0, count: 0 };
      if (t.amount_usd >= 0) row.in += t.amount_usd;
      else row.out -= t.amount_usd;
      row.count++;
      cats.set(k, row);
    }
    const kind = q.get("kind") ?? "all";
    const cat = q.get("category");
    const needle = (q.get("q") ?? "").toLocaleLowerCase("tr");
    let list = txs.filter((t) =>
      kind === "transfer" ? isTransfer(t) : kind === "in" ? !isTransfer(t) && t.amount > 0 : kind === "out" ? !isTransfer(t) && t.amount < 0 : kind === "uncategorized" ? !isTransfer(t) && !t.category_id : true,
    );
    if (cat) list = list.filter((t) => (t.category_id ?? "_none") === cat);
    if (needle) list = list.filter((t) => `${t.description} ${t.counterparty ?? ""}`.toLocaleLowerCase("tr").includes(needle));
    list = [...list].sort((a, b) => b.booked_at.localeCompare(a.booked_at) || b.id - a.id);
    const limit = Number(q.get("limit") ?? 100);
    const sum = (k: "in" | "out" | "net") => r2(m.reduce((s, x) => s + x[k], 0));
    return {
      months: m,
      categories: [...cats.values()].map((c) => ({ ...c, in: r2(c.in), out: r2(c.out) })).sort((a, b) => b.in + b.out - (a.in + a.out)),
      totals: { in: sum("in"), out: sum("out"), net: sum("net") },
      transactions: list.slice(0, limit),
      total: list.length,
      ...FX,
    };
  }

  if (path === "/api/finance/projection") {
    const weeks = Number(q.get("weeks") ?? 13);
    const weighted = q.get("weighted") !== "false";
    const floor = Number(q.get("floor") ?? 0);
    const rows = projection(ids, weeks, weighted);
    const low = rows.reduce((m, w) => (w.close < m.close ? w : m), rows[0]);
    const below = rows.find((w) => w.close < floor);
    return {
      start_cash_usd: r2(cash(ids)),
      weeks: rows,
      lowest: { week: low.week, close: low.close },
      first_below: below ? { week: below.week, close: below.close } : null,
      floor,
      weighted,
      items: PLANNED.filter((p) => ids.includes(p.company_id)).sort((a, b) => Number(a.status !== "open") - Number(b.status !== "open") || a.due_date.localeCompare(b.due_date)),
      ...FX,
    };
  }

  if (path === "/api/finance/intercompany") {
    const [start, end] = range(q);
    const ic = TXS.filter((t) => t.counterparty_company_id && t.booked_at >= start && t.booked_at <= end);
    const touches = (t: Tx) => ids.includes(t.company_id) || ids.includes(t.counterparty_company_id!);
    const visible = ic.filter(touches);
    const matrix = new Map<string, number>();
    for (const t of visible) {
      if (t.amount >= 0) continue;
      const k = `${t.company_id}>${t.counterparty_company_id}`;
      matrix.set(k, (matrix.get(k) ?? 0) - t.amount_usd);
    }
    const all: CompanyId[] = ["llc", "gmbh", "tr"];
    const pairs = [];
    for (let i = 0; i < 3; i++)
      for (let j = i + 1; j < 3; j++) {
        const a = all[i];
        const b = all[j];
        if (!ids.includes(a) && !ids.includes(b)) continue;
        const ab = matrix.get(`${a}>${b}`) ?? 0;
        const ba = matrix.get(`${b}>${a}`) ?? 0;
        pairs.push({ a, b, a_to_b: r2(ab), b_to_a: r2(ba), net: r2(ab - ba) });
      }
    const um = unmatched(ic);
    return {
      matrix: [...matrix.entries()].map(([k, v]) => {
        const [from, to] = k.split(">");
        return { from, to, usd: r2(v) };
      }),
      pairs,
      unmatched_outgoing: um.outgoing.filter(touches),
      unmatched_incoming: um.incoming.filter(touches),
      transactions: [...visible].sort((a, b) => b.booked_at.localeCompare(a.booked_at)),
      ...FX,
    };
  }

  if (path === "/api/finance/snapshots") {
    const inScope = SNAPSHOTS.filter((s) => ids.includes(s.company_id));
    const weeks = [...new Set(inScope.map((s) => s.week_date))].sort().reverse();
    const week = q.get("week") ?? weeks[0] ?? null;
    const cards = inScope.filter((s) => s.week_date === week);
    const t = (k: "assets_usd" | "liabilities_usd" | "net_usd") => r2(cards.reduce((s, c) => s + c[k], 0));
    return { weeks, week, snapshots: cards, totals: { assets_usd: t("assets_usd"), liabilities_usd: t("liabilities_usd"), net_usd: t("net_usd") }, missing_fx: [] };
  }

  if (path === "/api/finance/snapshots/summary") {
    return { text: "12 Eki 2026 haftalık finans özeti\n\nKONSOLİDE\nAktifler: $498.120\nPasifler: $86.400\nNet: $411.720\n" };
  }

  if (path === "/api/finance/decisions") {
    const visible = DECISIONS.filter((d) => !d.company_id || ids.includes(d.company_id)).map((d) => ({
      ...d,
      overdue: ["acik", "aksiyon", "devam"].includes(d.status) && !!d.due_date && d.due_date < TODAY,
    }));
    const active = visible.filter((d) => ["acik", "aksiyon", "devam"].includes(d.status));
    const rank = { yuksek: 0, orta: 1, dusuk: 2 };
    visible.sort((a, b) => Number(!active.includes(a)) - Number(!active.includes(b)) || rank[a.priority] - rank[b.priority] || (a.due_date ?? "9").localeCompare(b.due_date ?? "9"));
    return {
      decisions: visible,
      stats: {
        open: active.length,
        action: visible.filter((d) => d.status === "aksiyon").length,
        overdue: visible.filter((d) => d.overdue).length,
        impact_usd: active.reduce((s, d) => s + (d.impact_usd ?? 0), 0),
      },
    };
  }
  return undefined;
}

// Yazma uçları gövdeyi alamaz (vite ara katmanı); örnek olarak birkaç durumu değiştirir.
function mockWrite(method: string, path: string): unknown {
  if (path === "/api/finance/sync") {
    return {
      seconds: 6.4,
      fx: { ok: true, rows: 3 },
      accounts: ACCOUNTS.filter((a) => a.provider !== "csv" && a.provider !== "manual").map((a) => ({
        account_id: a.id, name: a.name, ok: !a.sync_error, inserted: a.sync_error ? undefined : 3, error: a.sync_error ?? undefined,
      })),
    };
  }
  if (path.endsWith("/statement/preview")) {
    return {
      currency: "TRY",
      header: ["Tarih", "Açıklama", "Etiket", "Tutar", "Bakiye", "Dekont No"],
      header_index: 4,
      mapping: { date: 0, description: 1, amount: 3, debit: null, credit: null, balance: 4 },
      count: 42,
      skipped: 1,
      duplicates: 30,
      date_from: addDays(TODAY, -14),
      date_to: TODAY,
      last_balance: 2_940_000,
      sample: [
        { booked_at: TODAY, description: "Trendyol hakediş", amount: 84_210.5, balance: 2_940_000 },
        { booked_at: TODAY, description: "Aras Kargo", amount: -12_480, balance: 2_855_789.5 },
        { booked_at: addDays(TODAY, -1), description: "SWIFT GELEN REXVEN LLC", amount: 1_225_000, balance: 2_868_269.5 },
      ],
    };
  }
  if (path.endsWith("/statement/import")) return { inserted: 12, duplicates: 30, skipped: 1, balance: 2_940_000 };
  if (method === "DELETE") return { id: 1 };
  if (path.endsWith("/lock")) {
    const s = SNAPSHOTS.find((x) => String(x.id) === path.split("/")[4]);
    if (s) {
      s.locked = true;
      s.locked_at = new Date().toISOString();
    }
    return { locked: true };
  }
  return { id: 1 };
}
