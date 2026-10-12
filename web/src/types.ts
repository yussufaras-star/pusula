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

export type SegmentKind = "dolu" | "mola" | "kayip" | "girilmemis";

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
  team: { today: Kpis; expected: Kpis };
  hours: HourRow[];
  reps: RepCard[];
  idle_thresholds: { warn: number; alert: number };
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
