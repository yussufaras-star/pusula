// /api/finance/* yanıtları (pusula/finance/views.py ile birebir).

export type CompanyId = "llc" | "gmbh" | "tr";
export type Scope = "all" | CompanyId;

export interface FxMeta {
  fx_date: string | null;
  fx: Record<string, number>;
  missing_fx: string[];
}

export interface Company {
  id: CompanyId;
  name: string;
  country: string;
  base_currency: string;
  match_patterns: string[];
  sort: number;
}

export interface Category {
  id: string;
  name: string;
  kind: "in" | "out" | "transfer";
  sort: number;
}

export interface Meta extends FxMeta {
  companies: Company[];
  categories: Category[];
}

export type Provider = "mercury" | "wise" | "stripe" | "revolut" | "csv" | "manual";
export type AccountType = "bank" | "fintech" | "payment" | "card" | "shipping" | "other";

export interface Account {
  id: number;
  company_id: CompanyId;
  name: string;
  institution: string;
  type: AccountType;
  currency: string;
  provider: Provider;
  provider_account_id: string | null;
  credential_key: string | null;
  balance: number;
  balance_usd: number;
  balance_at: string | null;
  last_synced_at: string | null;
  sync_error: string | null;
  sync_warning: string | null;
  active: boolean;
  sort: number;
}

export interface AccountsPayload extends FxMeta {
  accounts: Account[];
}

export interface WeekClose {
  week: string;
  close: number;
}

export interface Overview extends FxMeta {
  as_of: string;
  cash_usd: number;
  start_cash_usd: number;
  change_usd: number;
  net_flow_usd: number;
  companies: {
    id: CompanyId;
    name: string;
    currency: string;
    cash_usd: number;
    cash_local: number | null;
    accounts: number;
  }[];
  history: ({ day: string; total: number } & Partial<Record<CompanyId, number>>)[];
  projection: WeekClose[];
  lowest: WeekClose | null;
  alerts: {
    cash_gap: WeekClose | null;
    overdue: { count: number; usd: number };
    sync_errors: { id: number; name: string; error: string }[];
    stale: { id: number; name: string; last_synced_at: string | null }[];
    warnings: { id: number; name: string; warning: string }[];
  };
  open_actions: number;
}

export interface Tx {
  id: number;
  account_id: number;
  account: string;
  company_id: CompanyId;
  booked_at: string;
  amount: number;
  currency: string;
  amount_usd: number;
  description: string;
  counterparty: string | null;
  category_id: string | null;
  counterparty_company_id: CompanyId | null;
  source: "api" | "csv" | "manual";
  edited: boolean;
}

export interface Flows extends FxMeta {
  months: { month: string; in: number; out: number; net: number }[];
  categories: { id: string | null; name: string; in: number; out: number; count: number }[];
  totals: { in: number; out: number; net: number };
  transactions: Tx[];
  total: number;
}

export interface PlannedItem {
  id: number;
  company_id: CompanyId;
  direction: "in" | "out";
  title: string;
  counterparty: string | null;
  amount: number;
  currency: string;
  due_date: string;
  recurrence: "none" | "weekly" | "monthly";
  recur_until: string | null;
  probability: number;
  status: "open" | "done" | "cancelled";
  category_id: string | null;
  notes: string | null;
  amount_usd: number;
  overdue: boolean;
}

export interface ProjWeek {
  week: string;
  in: number;
  out: number;
  close: number;
  items: { item_id: number | null; title: string; company_id: CompanyId; day: string; usd: number; overdue: boolean }[];
}

export interface Projection extends FxMeta {
  start_cash_usd: number;
  weeks: ProjWeek[];
  lowest: WeekClose | null;
  first_below: WeekClose | null;
  floor: number;
  weighted: boolean;
  items: PlannedItem[];
}

export interface Intercompany extends FxMeta {
  matrix: { from: CompanyId; to: CompanyId; usd: number }[];
  pairs: { a: CompanyId; b: CompanyId; a_to_b: number; b_to_a: number; net: number }[];
  unmatched_outgoing: Tx[];
  unmatched_incoming: Tx[];
  transactions: Tx[];
}

export type SectionKey = "current_assets" | "noncurrent_assets" | "current_liabilities" | "longterm_liabilities" | "equity";

export interface SnapshotLine {
  id: number;
  snapshot_id: number;
  side: "asset" | "liability";
  section: SectionKey;
  group_name: string;
  label: string;
  amount: number;
  currency: string;
  origin: "account" | "planned" | "intercompany" | "manual";
  sort: number;
}

export interface Sheet {
  assets_usd: number;
  debts_usd: number;
  equity_usd: number;
  equity_entered_usd: number;
  computed_equity_usd: number;
  has_equity: boolean;
  liabilities_and_equity_usd: number;
  difference_usd: number;
  balanced: boolean;
  eliminated_receivables_usd: number;
  eliminated_payables_usd: number;
  elimination_gap_usd: number;
  sections: { key: SectionKey; title: string; side: "asset" | "liability"; usd: number; groups: { name: string; usd: number }[] }[];
  ratios: {
    current: number | null;
    acid_test: number | null;
    cash: number | null;
    working_capital_usd: number;
    debt_to_equity: number | null;
    equity_ratio: number | null;
  };
  missing_fx: string[];
}

export interface SheetBrief {
  week: string;
  assets_usd: number;
  debts_usd: number;
  equity_usd: number;
}

export interface SnapshotCard {
  id: number;
  company_id: CompanyId;
  week_date: string;
  locked: boolean;
  locked_at: string | null;
  notes: string;
  fx: Record<string, number>;
  sheet: Sheet;
  previous: SheetBrief | null;
  lines: SnapshotLine[];
}

export interface Snapshots {
  weeks: string[];
  week: string | null;
  snapshots: SnapshotCard[];
  consolidated: Sheet | null;
  previous_consolidated: SheetBrief | null;
  totals: { assets_usd: number; debts_usd: number; equity_usd: number };
  missing_fx: string[];
  catalog: { key: SectionKey; title: string; side: "asset" | "liability"; groups: string[] }[];
}

export type DecisionStatus = "acik" | "aksiyon" | "devam" | "tamam" | "iptal";
export type Priority = "yuksek" | "orta" | "dusuk";

export interface Decision {
  id: number;
  company_id: CompanyId | null;
  title: string;
  description: string;
  owner: string | null;
  status: DecisionStatus;
  priority: Priority;
  due_date: string | null;
  impact_usd: number | null;
  meeting_date: string | null;
  created_at: string | null;
  updated_at: string | null;
  overdue: boolean;
}

export interface Decisions {
  decisions: Decision[];
  stats: { open: number; action: number; overdue: number; impact_usd: number };
}

export interface StatementMapping {
  date: number;
  description: number | null;
  amount: number | null;
  debit: number | null;
  credit: number | null;
  balance: number | null;
}

export interface StatementPreview {
  currency: string;
  header: string[];
  header_index: number;
  mapping: StatementMapping | null;
  count: number;
  skipped: number;
  duplicates: number;
  date_from: string | null;
  date_to: string | null;
  last_balance: number | null;
  sample: { booked_at: string; description: string; amount: number; balance: number | null }[];
}

export interface SyncReport {
  skipped?: string;
  seconds?: number;
  fx?: { ok: boolean; rows?: number; error?: string };
  accounts?: { account_id: number; name: string; ok: boolean; inserted?: number; warning?: string; error?: string; skipped?: string }[];
}
