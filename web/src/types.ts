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
  muaf_dk: number;
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
  | "izinli"
  | "muaf";

export type SegmentKind = "dolu" | "toplanti" | "mola" | "kayip" | "girilmemis" | "muaf";

/** Panelden girilen muaf süre (eğitim vb.); doluluğa girmez. */
export interface Excused {
  id: number;
  start: string;
  end: string;
  reason: string | null;
}

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
  excused: Excused[];
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
  portfolio?: Portfolio | null;
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
export type TargetMetric = "arama" | "ulasilan" | "ulasma_orani" | "randevu" | "sure_ort" | "doluluk";
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
  kind?: "tempo" | "portfoy" | "randevu_ara" | "randevu_hatirlat" | "randevu_oda";
}

export interface NudgeQueue {
  items: NudgeItem[];
  approval_required: boolean;
  ai: boolean;
}

// pusula/portfolio.py load_portfolio.
export interface UntouchedLead {
  lead_id: string;
  rep_id: string;
  name: string | null;
  assigned_at: string | null;
  last_touch: string | null;
  hours: number | null;
}

export interface Portfolio {
  new_status: string;
  untouched_hours: number;
  by_status: { rep_id: string; status: string; count: number }[];
  untouched: UntouchedLead[];
}

// pusula/sales_report.py build_sales_report.
export interface ReportRow extends Kpis {
  rep_id?: string;
  name?: string;
  atanan: number;
  satis: number;
  ciro: number;
  dongu_gun: number | null;
  toplanti_donus: number | null;
  satis_orani: number | null;
  ort_satis: number | null;
  gunluk_arama: number;
  gunluk_ciro: number;
  work_days: number;
}

export interface CiroMonth {
  ay: string;
  ay_etiket: string;
  satis: number;
  ciro: number;
  ortalama: number | null;
  kisi_basi: number;
}

export interface SalesReport {
  start: string;
  end: string;
  data_start: string;
  work_days: number;
  reps: ReportRow[];
  team: ReportRow;
  avg: ReportRow;
  ciro_monthly: CiroMonth[];
  ciro_trend?: CiroTrend;
  /** Son başarılı Zoho senkronu. */
  data_as_of?: string | null;
}

// pusula/ciro_trend.py build_trend.
export interface ToDate {
  ciro: number;
  satis: number;
  onceki_ciro: number;
  onceki_satis: number;
  degisim: number | null;
  satis_degisim: number | null;
  bas: string;
  onceki_bas: string;
  onceki_bit: string;
}

export interface TrendPoint {
  satis: number;
  ciro: number;
  degisim: number | null;
  suruyor: boolean;
  bas?: string;
  bit?: string;
  ay?: string;
  ay_etiket?: string;
}

export interface CiroTrend {
  haftalar: TrendPoint[];
  aylar: TrendPoint[];
  ayni_gune: { hafta: ToDate; ay: ToDate };
  temsilci: Record<string, { hafta: ToDate; ay: ToDate }>;
}

// pusula/sales_report.py source_yield.
export interface SourceYieldRow {
  source: string | null;
  lead: number;
  dokunulmamis: number;
  ulasilamadi: number;
  ulasildi: number;
  musteri: number;
  satan_lead: number;
  satis: number;
  ciro: number;
  islenme: number | null;
  ulasma: number | null;
  satis_orani: number | null;
  lead_basi_ciro: number | null;
}

export interface SourceYieldPayload {
  dim: string;
  label: string;
  available: boolean;
  rows: SourceYieldRow[];
  total?: SourceYieldRow;
}

// pusula/commission.py load_commission.
export interface CommissionRep {
  rep_id: string;
  name: string;
  sayilan: number;
  toplam_satis: number;
  ciro: number;
  oran: number;
  prim: number;
  tahmin_sayilan: number;
  tahmin_ciro: number;
  tahmin_oran: number;
  tahmin_prim: number;
  sonraki: { esik: number; oran: number; kalan: number; gunluk_gereken: number | null; ek_prim: number } | null;
  ciro_sayilan: number;
  ciro_diger: number;
  diger_satis: number;
}

export interface CommissionPayload {
  month: string;
  /** Geçmiş gün görünümü: satışlar bu güne kadar. */
  asof?: string | null;
  available: boolean;
  elapsed_workdays: number;
  month_workdays: number;
  packages: string[];
  tiers: { esik: number; oran: number }[];
  reps: CommissionRep[];
  /** Zoho'dan satışların en son kontrol edildiği an. */
  satis_kontrol?: string | null;
}

// pusula/commission.py load_rep_deals.
export interface CommissionDeal {
  deal_id: string;
  paket: string | null;
  asama: string | null;
  tutar: number | null;
  kapanis: string | null;
  olusturma: string | null;
  owner_rep_id: string | null;
  musteri: string | null;
  guncellendi: string | null;
  ciro: boolean;
  sayilan: boolean;
  neden: string | null;
}

export interface CommissionDeals {
  month: string;
  rep_id: string | null;
  deals: CommissionDeal[];
}

// pusula/reco.py load_reco.
export interface RecoSegment {
  key: "satiyor_yenilememis" | "satiyor_yenilemis" | "satmiyor";
  label: string;
  musteri: number;
  abonelik_bitmis: number;
  bitis_30: number;
  bitis_90: number;
  abonelik_yok: number;
  reco_satis: number;
  reco_ciro: number;
  medyan_son1: number | null;
}

export interface RecoItem {
  contact_id: string;
  name: string | null;
  owner: string | null;
  paket: string | null;
  abonelik_bit: string | null;
  gun: number | null;
  son1_satis: number | null;
  toplam_satis: number | null;
}

export interface RecoPayload {
  available: boolean;
  owner?: string | null;
  owners?: [string, number][];
  bu_ay?: { owner: string; satis: number; ciro: number }[];
  segments?: RecoSegment[];
  toplam?: number;
  liste?: RecoItem[];
  liste_toplam?: number;
}

// pusula/churn.py load_churn.
export interface ChurnMonth {
  ay: string;
  vadesi_gelen: number;
  yeniledi: number;
  kaybedildi: number;
  geri_kazanildi: number;
  bekliyor: number;
  churn: number | null;
  iade: number;
  satis: number;
  iade_orani: number | null;
}

export interface ChurnPeriod {
  yeniledi: number;
  kaybedildi: number;
  geri_kazanildi: number;
  churn: number | null;
}

export interface ChurnPayload {
  available: boolean;
  aylar?: ChurnMonth[];
  son_12_ay?: ChurnPeriod;
  bu_yil?: ChurnPeriod;
  musteri?: number;
  packages?: string[];
  yaklasan?: ChurnItem[];
  kaybedilen?: ChurnItem[];
  yaklasan_gun?: number;
  kaybedilen_gun?: number;
  crm_oncesi?: number;
  baslangic?: string;
}

export interface ChurnItem {
  contact_id: string;
  musteri: string;
  sahip: string | null;
  paket: string | null;
  son_satis: string;
  satis_sayisi: number;
  vade: string;
  kalan_gun?: number;
  gecen_gun?: number;
  crm_oncesi?: boolean;
}
