// pusula/metrics.py finish() çıktısı ile birebir.
export interface Kpis {
  arama: number;
  giden_temas: number;
  donus: number;
  gelen: number;
  ulasilan: number;
  lead_payda: number;
  lead_pay: number;
  ulasma_orani: number | null;
  randevu: number;
  katildi: number;
  katilmadi: number;
  sonuc_girilmedi: number;
  katilim_orani: number | null;
  katilim_payda: number;
  whatsapp: number;
  sure_toplam: number;
  sure_ort: number | null;
  sure_tipik: number | null;
  call_sec: number;
  meet_dk: number;
  dolu_dk: number;
  mola_dk: number;
  kayip_dk: number;
  girilmemis_dk: number;
  toplanti_dk: number;
  mesai_dk: number;
  doluluk: number | null;
  n_reps: number;
}

export type RepStatus =
  | "aktif"
  | "toplantida"
  | "aramada"
  | "bosta"
  | "hareketsiz"
  | "baslamadi"
  | "mesai_disi"
  | "gecmis"
  | "izinli";

export type SegmentKind = "dolu" | "toplanti" | "mola" | "kayip" | "girilmemis";

export interface Segment {
  start: string;
  end: string;
  kind: SegmentKind;
}

export interface Gap extends Segment {
  minutes: number;
}

export interface RepCard {
  rep_id: string;
  name: string;
  today: Kpis;
  expected: Kpis;
  first_at: string | null;
  last_active_at: string | null;
  last_call_at: string | null;
  last_meeting_at: string | null;
  last_whatsapp_at: string | null;
  last_crm_at: string | null;
  idle_min: number | null;
  status: RepStatus;
  on_leave: boolean;
  hours: HourRow[];
  timeline: Segment[];
  gaps: Gap[];
  target?: Targets;
}

export interface HourRow {
  hour: number;
  done: boolean;
  today: Kpis;
  expected: Kpis;
}

export interface LivePayload {
  day: string;
  now: string;
  is_today: boolean;
  in_shift: boolean;
  data_as_of: string | null;
  crm_tracking: boolean;
  history_days: number;
  team: { today: Kpis; expected: Kpis; target?: Targets };
  hours: HourRow[];
  reps: RepCard[];
  idle_thresholds: { warn: number; alert: number };
  meetings?: Meetings;
}

export interface Me {
  email: string;
  role: "admin" | "rep";
  rep_id: string | null;
  full_name: string | null;
}

// pusula/compare.py build_side çıktısı.
export interface ComparePoint {
  bucket: number | string;
  partial: boolean;
  kpis: Kpis;
}

export interface CompareSide {
  label: string;
  subject: string;
  first: string;
  last: string;
  cut: string;
  aligned: boolean;
  work_days: number;
  n_reps: number;
  kpis: Kpis;
  series_group: "hour" | "day";
  series: ComparePoint[];
}

export interface ComparePayload {
  now: string;
  a: CompareSide;
  b: CompareSide;
}

export interface RepRef {
  rep_id: string;
  full_name: string;
}

// pusula/meetings.py classify / summarize çıktısı.
export type MeetingState = "geldi" | "gelmedi" | "bekleniyor" | "suruyor" | "bilinmiyor" | "iptal";

export interface MeetingItem {
  rep_id: string;
  start: string;
  planned_min: number;
  customer: string | null;
  state: MeetingState;
  source: "zoho_meeting" | "crm" | "yok";
  crm_status: string;
  result_entered: boolean;
  mismatch: boolean;
  minutes: number;
  call_after: string | null;
  wa_after: string | null;
  followed_up: boolean;
}

export interface MeetingSummary {
  planlanan: number;
  gelen: number;
  gelmeyen: number;
  bilinmiyor: number;
  bekleyen: number;
  iptal: number;
  sonuc_girilen: number;
  sonuc_girilmemis: number;
  toplam_dk: number;
  aranan: number;
  aranmayan: number;
  uyumsuz: number;
  katilim_orani: number | null;
}

export interface Meetings {
  items: MeetingItem[];
  by_rep: Record<string, MeetingSummary>;
}

// pusula/targets.py — kural hedefleri ve "bu saate kadar" payı.
export type TargetMetric = "arama" | "ulasilan" | "ulasma_orani" | "randevu" | "doluluk";
export type TargetSource = "standart" | "ortalama";

export interface TargetItem {
  daily: number;
  by_now: number;
  source: TargetSource;
  actual: number | null;
  bonus?: number;
}

export type Targets = Partial<Record<TargetMetric, TargetItem>>;

// pusula/pace_nudges.py list_queue.
export type NudgeStatus = "bekliyor" | "gonderiliyor" | "gonderildi" | "reddedildi" | "suresi_doldu" | "hata";

export interface NudgeItem {
  id: number;
  rep_id: string;
  rep_name: string | null;
  created_at: string;
  body: string;
  ai_text: string | null;
  text: string;
  status: NudgeStatus;
  decided_by: string | null;
  sent_at: string | null;
  failure: string | null;
  shadow: boolean | null;
}

export interface NudgeQueue {
  items: NudgeItem[];
  approval_required: boolean;
  ai: boolean;
}
