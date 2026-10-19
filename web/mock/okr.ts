// Yalnız `npm run dev:mock`: Ekip Panosu örnek verisi (2026 Ç4 taslağına benzer).
import type { OkrBoard, OkrKeyResult, OkrObjective } from "../src/types";

const at = (d: number) => new Date(Date.UTC(2026, 9, d, 9)).toISOString();

function kr(id: number, objective_id: number, p: Partial<OkrKeyResult>): OkrKeyResult {
  return {
    id,
    objective_id,
    title: "",
    metric: null,
    metric_label: null,
    unit: "sayi",
    direction: "up",
    start_value: 0,
    target_value: 1,
    owner_rep_id: null,
    owner_name: null,
    sort: id,
    source: "otomatik",
    current: null,
    progress: null,
    score: null,
    expected: null,
    status: "yolunda",
    confidence: null,
    checkin_due: false,
    checkins: [],
    ...p,
  };
}

function obj(p: Partial<OkrObjective> & Pick<OkrObjective, "id" | "title" | "krs">): OkrObjective {
  const known = p.krs.map((k) => k.score).filter((s): s is number => s !== null);
  const score = known.length ? Math.round((known.reduce((a, b) => a + b, 0) / known.length) * 100) / 100 : null;
  return {
    year: 2026,
    quarter: 4,
    owner: "team",
    owner_name: "Ekip",
    description: null,
    kind: "taahhut",
    status: "aktif",
    parent_id: null,
    parent_title: null,
    children: 0,
    final_score: null,
    reflection: null,
    sort: 0,
    score,
    tone: score === null ? "neutral" : score >= 0.7 ? "good" : score >= 0.4 ? "warning" : "critical",
    bar: p.kind === "iddiali" ? 0.7 : 1,
    ...p,
  };
}

export function mockOkr(): OkrBoard {
  const objectives = [
    obj({
      id: 1,
      title: "Eylül ivmesini kalıcı yap: yılı rekor çeyrekle kapat",
      description: "Eylül'de 3,36 M TL ile en iyi ayımızı yaptık. Bu tempo tesadüf değil, standart olmalı.",
      children: 1,
      krs: [
        kr(1, 1, { title: "Çeyrek cirosu 10 M TL", metric: "ciro", metric_label: "Ciro", unit: "tl", target_value: 10_000_000, current: 1_996_347, progress: 0.2, score: 0.2, expected: 0.2, status: "yolunda", confidence: 7, checkins: [{ id: 1, kr_id: 1, value: null, confidence: 7, note: "Ekim ilk yarı 2 M; kampanya ikinci yarıda", author: "Yusuf Aras", created_at: at(13) }] }),
        kr(2, 1, { title: "270 satış kapat", metric: "satis", metric_label: "Satış adedi", unit: "adet", target_value: 270, current: 47, progress: 0.17, score: 0.17, expected: 0.2, status: "yolunda", checkin_due: true }),
        kr(3, 1, { title: "Ortalama satış tutarını 36,4 binden 40 bin TL'ye çıkar", metric: "ort_satis", metric_label: "Ortalama satış tutarı", unit: "tl", start_value: 36_400, target_value: 40_000, current: 42_475, progress: 1, score: 1, expected: 1, status: "tamam" }),
      ],
    }),
    obj({
      id: 2,
      kind: "iddiali",
      title: "Her toplantı satışa yakın bir toplantı olsun",
      krs: [
        kr(4, 2, { title: "Toplantı katılım oranı %36'dan %55'e", metric: "katilim_orani", metric_label: "Toplantı katılım oranı", unit: "yuzde", start_value: 36, target_value: 55, current: 46.9, progress: 0.57, score: 0.57, expected: 0.7, status: "risk", confidence: 5 }),
        kr(5, 2, { title: "Ulaşılan lead'den toplantıya dönüş %15,6'dan %22'ye", metric: "toplanti_donus", metric_label: "Toplantıya dönüş", unit: "yuzde", start_value: 15.6, target_value: 22, current: 17.6, progress: 0.31, score: 0.31, expected: 0.7, status: "geride" }),
        kr(6, 2, { title: "Satışların %30'u toplantı sonrası", source: "elle", unit: "yuzde", target_value: 30, current: 18, progress: 0.6, score: 0.6, expected: 0.7, status: "yolunda", owner_rep_id: "r1", owner_name: "Ayşe Yılmaz", confidence: 6 }),
      ],
    }),
    obj({
      id: 3,
      owner: "r1",
      owner_name: "Ayşe Yılmaz",
      kind: "iddiali",
      parent_id: 1,
      parent_title: "Eylül ivmesini kalıcı yap: yılı rekor çeyrekle kapat",
      title: "Kendi rekorumu kır: 2,5 M TL çeyrek",
      krs: [
        kr(7, 3, { title: "Kişisel ciro 2,5 M TL", metric: "ciro", metric_label: "Ciro", unit: "tl", target_value: 2_500_000, current: 302_966, progress: 0.12, score: 0.12, expected: 0.14, status: "yolunda" }),
        kr(8, 3, { title: "Ort. görüşme süresi 300 sn", metric: "sure_ort", metric_label: "Ort. görüşme süresi", unit: "sn", start_value: 236, target_value: 300, current: 281, progress: 0.7, score: 0.7, expected: 0.7, status: "yolunda" }),
      ],
    }),
  ];
  const all = objectives.flatMap((o) => o.krs);
  return {
    year: 2026,
    quarter: 4,
    label: "2026 4. çeyrek",
    first: "2026-10-01",
    last: "2026-12-31",
    share: 0.2,
    state: "suruyor",
    objectives,
    annual: [],
    aligned_parents: [{ id: 1, title: objectives[0].title, owner: "team", quarter: 4 }],
    counts: { "0": 0, "1": 0, "2": 0, "3": 0, "4": 3 },
    summary: {
      team_score: 0.48,
      objectives: 3,
      krs: all.length,
      on_track: all.filter((k) => k.status === "yolunda" || k.status === "tamam").length,
      at_risk: all.filter((k) => k.status === "risk" || k.status === "geride").length,
      checkin_due: all.filter((k) => k.checkin_due).length,
    },
    warnings: [],
    cfr: [
      { id: 1, kind: "takdir", rep_id: "r3", rep_name: "Zeynep Demir", objective_id: null, text: "Cuma günü 3 toplantıyı aynı gün satışa çevirdin, harika takip.", author: "Yusuf Aras", created_at: at(17) },
      { id: 2, kind: "konusma", rep_id: "r1", rep_name: "Ayşe Yılmaz", objective_id: 3, text: "Görüşme süresi arttı; kapanış cümlesini birlikte çalışacağız.", author: "Yusuf Aras", created_at: at(14) },
    ],
    reps: [
      { rep_id: "r1", name: "Ayşe Yılmaz" },
      { rep_id: "r2", name: "Mehmet Kaya" },
      { rep_id: "r3", name: "Zeynep Demir" },
    ],
    metrics: [
      { key: "ciro", label: "Ciro", unit: "tl", cumulative: true, direction: "up" },
      { key: "satis", label: "Satış adedi", unit: "adet", cumulative: true, direction: "up" },
      { key: "katilim_orani", label: "Toplantı katılım oranı", unit: "yuzde", cumulative: false, direction: "up" },
      { key: "dongu_gun", label: "Satış döngüsü (medyan gün)", unit: "gun", cumulative: false, direction: "down" },
    ],
    can_edit_team: true,
    template_available: false,
    today: "2026-10-19",
  };
}
