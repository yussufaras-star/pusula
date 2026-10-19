"""Ekip Panosu: OKR (Objectives and Key Results), John Doerr usulü.

Measure What Matters'ın dört süper gücü panoda karşılık bulur:

- Odak: çeyrek başına sahip (ekip ya da temsilci) en fazla 5 hedef, hedef
  başına 2-5 anahtar sonuç. Aşılınca uyarı verilir, engellenmez.
- Hizalama: çeyrek hedefi yılın hedeflerinden birine bağlanır (parent_id).
  Temsilcinin kişisel hedefi aşağıdan yukarı yazılır, ekip hedefine bağlanır.
- Takip: anahtar sonuç ya pusula.sales_report'tan otomatik ölçülür (ciro,
  satış, ulaşma oranı ...) ya da haftalık check-in'de elle girilir. Her
  check-in bir güven puanı (1-10) ve not taşır.
- Esneme: hedef "taahhüt" (1,0 beklenir) ya da "iddialı"dır (0,7 başarı
  sayılır). Puan 0,0-1,0; 0,7+ yeşil, 0,4-0,6 sarı, altı kırmızı.

CFR (konuşma, geri bildirim, takdir) aynı panoda tutulur. Takdir herkese
açıktır; konuşma ve geri bildirim yalnız yönetici ile ilgili temsilcide.

Tablolar ilk yazmada oluşur; yoksa okuma boş döner (pusula.leaves gibi).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from psycopg.types.json import Json

from pusula.config import get_org_id
from pusula.panel_data import connect

TEAM = "team"
KINDS = ("taahhut", "iddiali")
STATUSES = ("taslak", "aktif", "kapandi")
CFR_KINDS = ("konusma", "geri_bildirim", "takdir")
UNITS = ("tl", "adet", "yuzde", "sn", "gun", "sayi")
MAX_OBJECTIVES = 5
MIN_KRS, MAX_KRS = 2, 5
# İddialı hedefte 0,7 başarıdır (Doerr: "aspirational OKR'lar %70'te tatmin eder").
BAR = {"taahhut": 1.0, "iddiali": 0.7}
STALE_DAYS = 7
QUARTER_LABEL = {0: "Yıllık", 1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}


@dataclass(frozen=True)
class Metric:
    label: str
    unit: str
    # Birikimli metrik dönem boyunca sıfırdan büyür (ciro, satış); başlangıcı
    # 0'dır. Oranların başlangıcı önceki dönemin değeridir.
    cumulative: bool
    direction: str = "up"


# Anahtarlar pusula.sales_report satır alanlarıdır.
METRICS: dict[str, Metric] = {
    "ciro": Metric("Ciro", "tl", True),
    "satis": Metric("Yeni müşteri (Premium / Plus)", "adet", True),
    "gelen_lead": Metric("Gelen lead (Zoho Leads)", "adet", True),
    "atanan": Metric("Atanan lead", "adet", True),
    "arama": Metric("Arama", "adet", True),
    "ulasilan": Metric("Ulaşılan", "adet", True),
    "randevu": Metric("Toplantı", "adet", True),
    "katildi": Metric("Katılan toplantı", "adet", True),
    "ort_satis": Metric("Ortalama satış tutarı", "tl", False),
    "ulasma_orani": Metric("Ulaşma oranı", "yuzde", False),
    "katilim_orani": Metric("Toplantı katılım oranı", "yuzde", False),
    "toplanti_donus": Metric("Toplantıya dönüş", "yuzde", False),
    "satis_orani": Metric("Take (yeni müşteri / ulaşılan lead)", "yuzde", False),
    "doluluk": Metric("Doluluk", "yuzde", False),
    "sure_ort": Metric("Ort. görüşme süresi", "sn", False),
    "gunluk_arama": Metric("Günlük arama", "sayi", False),
    "dongu_gun": Metric("Satış döngüsü (medyan gün)", "gun", False, "down"),
}


# --- dönem --------------------------------------------------------------------


def period_bounds(year: int, quarter: int) -> tuple[date, date]:
    """quarter 0 = yılın tamamı. Her iki uç dahil."""
    if quarter == 0:
        return date(year, 1, 1), date(year, 12, 31)
    first = date(year, 3 * quarter - 2, 1)
    nxt = date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)
    return first, nxt - timedelta(days=1)


def current_quarter(today: date) -> int:
    return (today.month - 1) // 3 + 1


def elapsed_share(first: date, last: date, today: date) -> float:
    """Dönemin geçen oranı (takvim günü; bugün yarım sayılır)."""
    if today < first:
        return 0.0
    if today > last:
        return 1.0
    total = (last - first).days + 1
    return round(((today - first).days + 0.5) / total, 4)


def _next_month(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def phased_share(first: date, last: date, today: date, weights: list[float] | None) -> float:
    """Aylık plan dağılımına göre geçen pay. weights dönemin her ayı için bir
    değerdir (ör. aylık ciro hedefi); uymazsa takvim günü oranına düşer."""
    months = []
    day = first
    while day <= last:
        months.append(day)
        day = _next_month(day)
    if not weights or len(weights) != len(months) or sum(weights) <= 0:
        return elapsed_share(first, last, today)
    if today < first:
        return 0.0
    if today > last:
        return 1.0
    done = 0.0
    for month, weight in zip(months, weights):
        month_last = _next_month(month) - timedelta(days=1)
        if today > month_last:
            done += weight
        elif today >= month:
            done += weight * ((today - month).days + 0.5) / ((month_last - month).days + 1)
    return round(done / sum(weights), 4)


def period_state(first: date, last: date, today: date) -> str:
    if today < first:
        return "gelecek"
    return "bitti" if today > last else "suruyor"


# --- puanlama ---------------------------------------------------------------


def kr_progress(start: float, target: float, current: float | None, direction: str = "up") -> float | None:
    """Başlangıçtan hedefe kat edilen yol, 0..1. Yön 'down'da azalmak iyidir."""
    if current is None:
        return None
    if target == start:
        good = current <= target if direction == "down" else current >= target
        return 1.0 if good else 0.0
    share = (current - start) / (target - start)
    return round(min(max(share, 0.0), 1.0), 4)


def grade_tone(score: float | None) -> str:
    """Doerr ölçeği: 0,7-1,0 yeşil, 0,4-0,6 sarı, 0,0-0,3 kırmızı."""
    if score is None:
        return "neutral"
    if score >= 0.7:
        return "good"
    return "warning" if score >= 0.4 else "critical"


def kr_status(
    progress: float | None,
    *,
    kind: str,
    share: float,
    state: str,
) -> tuple[str, float | None]:
    """(durum, beklenen ilerleme). Beklenen = çıta × geçen süre: başlangıçtan
    hedefe dönem boyunca doğrusal yürünür (oranlarda da, elle ölçülenlerde de)."""
    if state == "gelecek":
        return "baslamadi", None
    bar = BAR.get(kind, 1.0)
    expected = round(bar * share, 4)
    if progress is None:
        return "veri_yok", expected
    if progress >= 1.0:
        return "tamam", expected
    if state == "bitti":
        return ("tamam" if progress >= bar else "kacti"), expected
    if progress >= expected * 0.9:
        return "yolunda", expected
    return ("risk" if progress >= expected * 0.6 else "geride"), expected


STATUS_TONE = {
    "yolunda": "good", "tamam": "good", "risk": "warning", "geride": "critical",
    "kacti": "critical", "baslamadi": "neutral", "veri_yok": "neutral",
}


def pace_tone(statuses: Iterable[str]) -> str:
    """Dönem sürerken hedefin rengi tempodan gelir; 0-1 ölçeği kapanış içindir."""
    tones = {STATUS_TONE.get(s, "neutral") for s in statuses}
    for tone in ("critical", "warning", "good"):
        if tone in tones:
            return tone
    return "neutral"


def objective_score(scores: Iterable[float | None]) -> float | None:
    """Hedef puanı anahtar sonuç puanlarının ortalamasıdır (Google/Doerr)."""
    known = [s for s in scores if s is not None]
    return round(sum(known) / len(known), 2) if known else None


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def kr_view(
    kr: dict[str, Any],
    *,
    kind: str,
    checkins: list[dict[str, Any]],
    values: dict[str, Any] | None,
    share: float,
    state: str,
    today: date,
    first: date | None = None,
    last: date | None = None,
    override: float | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Anahtar sonucun panodaki hâli. checkins yeniden eskiye sıralı.

    Otomatik ölçülen sonuçta veri yoksa (ör. CRM taşıma öncesi) son check-in
    değeri kullanılır (source 'yedek'). override yıllık toplam gibi dışarıda
    hesaplanan değerdir.
    """
    metric = METRICS.get(kr.get("metric") or "")
    start = float(kr["start_value"] or 0)
    target = float(kr["target_value"])
    direction = metric.direction if metric else (kr.get("direction") or "up")
    manual = next((c for c in checkins if c.get("value") is not None), None)
    if override is not None:
        source, current = "otomatik", override
    elif metric:
        source = "otomatik"
        current = _num((values or {}).get(kr["metric"])) if state != "gelecek" else None
        if current is None and state != "gelecek":
            source = "yedek"
            current = _num(manual["value"]) if manual else None
    else:
        source = "elle"
        current = _num(manual["value"]) if manual else None
    phasing = kr.get("phasing")
    if phasing and first is not None and last is not None:
        share = phased_share(first, last, today, [float(w) for w in phasing])
    progress = kr_progress(start, target, current, direction)
    status, expected = kr_status(
        progress,
        kind=kind,
        share=share,
        state=state,
    )
    last = checkins[0] if checkins else None
    confidence = next((c["confidence"] for c in checkins if c.get("confidence") is not None), None)
    last_at = last["created_at"] if last else None
    if isinstance(last_at, datetime):
        last_day = last_at.date()
    elif isinstance(last_at, str):
        last_day = date.fromisoformat(last_at[:10])
    else:
        last_day = None
    stale = state == "suruyor" and (last_day is None or (today - last_day).days >= STALE_DAYS)
    return {
        **kr,
        "start_value": start,
        "target_value": target,
        "direction": direction,
        "unit": metric.unit if metric else kr.get("unit") or "sayi",
        "metric_label": metric.label if metric else None,
        "source": source,
        "note": note,
        "current": current,
        "progress": progress,
        "score": None if progress is None else round(progress, 2),
        "expected": expected,
        "status": status,
        "confidence": confidence,
        "checkin_due": stale,
        "checkins": checkins[:6],
    }


MONTHS_TR = (
    "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
    "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
)


def tr_day(day: date) -> str:
    return f"{day.day} {MONTHS_TR[day.month - 1]}"


ROLLUP_METRICS = ("ciro", "satis", "gelen_lead")


def annual_rollup(
    *,
    objectives: list[dict[str, Any]],
    krs: list[dict[str, Any]],
    checkins: list[dict[str, Any]],
    quarter_values: dict[int, dict[str, float | None]],
) -> dict[str, tuple[float, list[int]]]:
    """Yıllık ekip ciro/satış/lead'i = başlamış çeyreklerin toplamı.

    Çeyreğin değeri otomatik ölçümdür; ölçüm yoksa o çeyrekteki aynı
    metrikli ekip anahtar sonucunun son check-in değeridir. İkisi de yoksa
    çeyrek eksik sayılır. quarter_values yalnız başlamış çeyrekleri taşır.
    """
    quarter_of = {o["id"]: o["quarter"] for o in objectives if o["owner"] == TEAM and o["quarter"]}
    latest = sorted(checkins, key=lambda c: str(c["created_at"]), reverse=True)
    manual: dict[tuple[int, str], float] = {}
    for kr in krs:
        q = quarter_of.get(kr["objective_id"])
        metric = kr.get("metric")
        if q is None or metric not in ROLLUP_METRICS or (q, metric) in manual:
            continue
        value = next((c["value"] for c in latest if c["kr_id"] == kr["id"] and c.get("value") is not None), None)
        if value is not None:
            manual[(q, metric)] = float(value)
    out: dict[str, tuple[float, list[int]]] = {}
    for metric in ROLLUP_METRICS:
        total, missing = 0.0, []
        for q in sorted(quarter_values):
            value = quarter_values[q].get(metric)
            if value is None:
                value = manual.get((q, metric))
            if value is None:
                missing.append(q)
            else:
                total += float(value)
        # Hiçbir çeyrekte değer yoksa toplam 0 değil "veri yok"tur.
        if len(missing) < len(quarter_values):
            out[metric] = (round(total, 2), missing)
    return out


def rule_warnings(objectives: list[dict[str, Any]], names: dict[str, str]) -> list[str]:
    """Odak kuralları (Doerr): az hedef, hedef başına 2-5 ölçülebilir sonuç."""
    out: list[str] = []
    per_owner: dict[str, int] = {}
    for obj in objectives:
        per_owner[obj["owner"]] = per_owner.get(obj["owner"], 0) + 1
        n = len(obj.get("krs") or [])
        if n == 0:
            out.append(f"“{obj['title']}” ölçülmüyor: anahtar sonuç ekle (sayısı yoksa anahtar sonuç değildir).")
        elif n < MIN_KRS:
            out.append(f"“{obj['title']}” tek anahtar sonuca dayanıyor; 2-5 arası önerilir.")
        elif n > MAX_KRS:
            out.append(f"“{obj['title']}” {n} anahtar sonuç taşıyor; en fazla {MAX_KRS} — odak kaybolur.")
    for owner, n in per_owner.items():
        if n > MAX_OBJECTIVES:
            who = "Ekip" if owner == TEAM else names.get(owner, owner)
            out.append(f"{who} için {n} hedef var; en fazla {MAX_OBJECTIVES}. Doerr: her şey önemliyse hiçbir şey önemli değildir.")
    return out


def build_board(
    *,
    year: int,
    quarter: int,
    objectives: list[dict[str, Any]],
    krs: list[dict[str, Any]],
    checkins: list[dict[str, Any]],
    values: dict[str, dict[str, Any]],
    names: dict[str, str],
    today: date,
    coverage: dict[str, date] | None = None,
    rollups: dict[str, tuple[float, list[int]]] | None = None,
) -> dict[str, Any]:
    """Saf: yılın tüm satırlarından seçili dönemin panosu.

    objectives yılın tamamıdır (hizalama için yıllık hedefler gerekir);
    values sahip → sales_report satırı ('team' ekip toplamı). coverage:
    metrik → ölçümün başladığı gün (dönem başından sonraysa not düşülür).
    rollups: yıllık ekip sonucu için metrik → (çeyrek toplamı, eksik çeyrekler).
    """
    first, last = period_bounds(year, quarter)
    share = elapsed_share(first, last, today)
    state = period_state(first, last, today)
    by_obj: dict[int, list[dict[str, Any]]] = {}
    for kr in sorted(krs, key=lambda k: (k.get("sort") or 0, k["id"])):
        by_obj.setdefault(kr["objective_id"], []).append(kr)
    by_kr: dict[int, list[dict[str, Any]]] = {}
    for c in sorted(checkins, key=lambda c: str(c["created_at"]), reverse=True):
        by_kr.setdefault(c["kr_id"], []).append(c)
    titles = {o["id"]: o["title"] for o in objectives}

    shown = []
    for obj in sorted(objectives, key=lambda o: (o["owner"] != TEAM, o.get("sort") or 0, o["id"])):
        if obj["quarter"] != quarter:
            continue
        kind = obj.get("kind") or "taahhut"
        views = []
        for kr in by_obj.get(obj["id"], []):
            override, note = None, None
            metric = kr.get("metric")
            if rollups and obj["owner"] == TEAM and metric in rollups:
                override, missing = rollups[metric]
                note = "Çeyreklerin toplamı"
                if missing:
                    quarters = ", ".join(f"Q{q}" for q in missing)
                    note += f"; {quarters} eksik — o çeyreğin sonucuna check-in ile gir"
            elif metric and coverage and coverage.get(metric) and coverage[metric] > first:
                note = f"{tr_day(coverage[metric])} tarihinden beri ölçülüyor"
            views.append(
                kr_view(
                    kr,
                    kind=kind,
                    checkins=by_kr.get(kr["id"], []),
                    values=values.get(obj["owner"]),
                    share=share,
                    state=state,
                    today=today,
                    first=first,
                    last=last,
                    override=override,
                    note=note,
                )
            )
        for view in views:
            view["owner_name"] = names.get(view.get("owner_rep_id") or "")
        score = objective_score(v["score"] for v in views)
        final = _num(obj.get("final_score"))
        shown.append(
            {
                **obj,
                "final_score": final,
                "owner_name": "Ekip" if obj["owner"] == TEAM else names.get(obj["owner"], obj["owner"]),
                "parent_title": titles.get(obj.get("parent_id")),
                "children": sum(1 for o in objectives if o.get("parent_id") == obj["id"]),
                "krs": views,
                "score": score,
                "tone": (
                    grade_tone(final if final is not None else score)
                    if final is not None or state == "bitti"
                    else pace_tone(v["status"] for v in views)
                ),
                "bar": BAR.get(kind, 1.0),
            }
        )

    counts = {q: sum(1 for o in objectives if o["quarter"] == q) for q in range(5)}
    all_krs = [kr for obj in shown for kr in obj["krs"]]
    team_scores = [o["score"] for o in shown if o["owner"] == TEAM]
    return {
        "year": year,
        "quarter": quarter,
        "label": f"{year} {QUARTER_LABEL[quarter]}",
        "first": first,
        "last": last,
        "share": share,
        "state": state,
        "objectives": shown,
        "annual": [
            {"id": o["id"], "title": o["title"], "owner": o["owner"]}
            for o in objectives
            if o["quarter"] == 0
        ],
        "aligned_parents": [
            {"id": o["id"], "title": o["title"], "owner": o["owner"], "quarter": o["quarter"]}
            for o in objectives
            if o["owner"] == TEAM and (o["quarter"] in (0, quarter))
        ],
        "counts": counts,
        "summary": {
            "team_score": objective_score(team_scores),
            "objectives": len(shown),
            "krs": len(all_krs),
            "on_track": sum(1 for k in all_krs if k["status"] in ("yolunda", "tamam")),
            "at_risk": sum(1 for k in all_krs if k["status"] in ("risk", "geride", "kacti")),
            "checkin_due": sum(1 for k in all_krs if k["checkin_due"]),
        },
        "warnings": rule_warnings(shown, names),
    }


def visible(objective_owner: str, rep_id: str | None) -> bool:
    """Temsilci ekip hedeflerini ve kendi kişisel hedeflerini görür."""
    return rep_id is None or objective_owner in (TEAM, rep_id)


def cfr_visible(row: dict[str, Any], rep_id: str | None) -> bool:
    if rep_id is None or row["kind"] == "takdir":
        return True
    return row.get("rep_id") == rep_id


# --- veritabanı -----------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS okr_objectives (
    id           bigserial PRIMARY KEY,
    org_id       text NOT NULL DEFAULT 'rexven',
    year         int NOT NULL CHECK (year BETWEEN 2020 AND 2100),
    quarter      int NOT NULL CHECK (quarter BETWEEN 0 AND 4),
    owner        text NOT NULL,
    title        text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    description  text,
    kind         text NOT NULL DEFAULT 'taahhut' CHECK (kind IN ('taahhut', 'iddiali')),
    status       text NOT NULL DEFAULT 'aktif' CHECK (status IN ('taslak', 'aktif', 'kapandi')),
    parent_id    bigint REFERENCES okr_objectives (id) ON DELETE SET NULL,
    final_score  numeric(3, 2) CHECK (final_score BETWEEN 0 AND 1),
    reflection   text,
    sort         int NOT NULL DEFAULT 0,
    created_by   text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS okr_objectives_period ON okr_objectives (org_id, year, quarter);
CREATE TABLE IF NOT EXISTS okr_key_results (
    id            bigserial PRIMARY KEY,
    objective_id  bigint NOT NULL REFERENCES okr_objectives (id) ON DELETE CASCADE,
    title         text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    metric        text,
    unit          text NOT NULL DEFAULT 'sayi',
    direction     text NOT NULL DEFAULT 'up' CHECK (direction IN ('up', 'down')),
    start_value   numeric(18, 2) NOT NULL DEFAULT 0,
    target_value  numeric(18, 2) NOT NULL,
    owner_rep_id  text,
    phasing       jsonb,
    sort          int NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE okr_key_results ADD COLUMN IF NOT EXISTS phasing jsonb;
CREATE INDEX IF NOT EXISTS okr_key_results_obj ON okr_key_results (objective_id);
CREATE TABLE IF NOT EXISTS okr_checkins (
    id          bigserial PRIMARY KEY,
    kr_id       bigint NOT NULL REFERENCES okr_key_results (id) ON DELETE CASCADE,
    value       numeric(18, 2),
    confidence  int CHECK (confidence BETWEEN 1 AND 10),
    note        text,
    author      text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS okr_checkins_kr ON okr_checkins (kr_id, created_at DESC);
CREATE TABLE IF NOT EXISTS okr_cfr (
    id            bigserial PRIMARY KEY,
    org_id        text NOT NULL DEFAULT 'rexven',
    kind          text NOT NULL CHECK (kind IN ('konusma', 'geri_bildirim', 'takdir')),
    rep_id        text,
    objective_id  bigint REFERENCES okr_objectives (id) ON DELETE SET NULL,
    text          text NOT NULL CHECK (length(text) BETWEEN 1 AND 2000),
    author        text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS okr_cfr_org ON okr_cfr (org_id, created_at DESC);
ALTER TABLE okr_objectives ENABLE ROW LEVEL SECURITY;
ALTER TABLE okr_key_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE okr_checkins ENABLE ROW LEVEL SECURITY;
ALTER TABLE okr_cfr ENABLE ROW LEVEL SECURITY;
"""

OBJECTIVE_FIELDS = (
    "year", "quarter", "owner", "title", "description", "kind", "status",
    "parent_id", "final_score", "reflection", "sort",
)
KR_FIELDS = (
    "objective_id", "title", "metric", "unit", "direction", "start_value",
    "target_value", "owner_rep_id", "phasing", "sort",
)


def _ready(conn: Any) -> bool:
    row = conn.execute("SELECT to_regclass('okr_objectives')").fetchone()
    return bool(row and row[0] is not None)


def _rows(cur: Any) -> list[dict[str, Any]]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_year(year: int) -> dict[str, list[dict[str, Any]]]:
    """Yılın hedefleri, anahtar sonuçları, check-in'leri ve CFR notları."""
    empty: dict[str, list[dict[str, Any]]] = {"objectives": [], "krs": [], "checkins": [], "cfr": []}
    with connect() as conn:
        if not _ready(conn):
            return empty
        org = get_org_id()
        objectives = _rows(
            conn.execute(
                f"SELECT id, {', '.join(OBJECTIVE_FIELDS)}, created_by, updated_at "
                "FROM okr_objectives WHERE org_id = %s AND year = %s",
                (org, year),
            )
        )
        ids = [o["id"] for o in objectives]
        if not ids:
            krs: list[dict[str, Any]] = []
            checkins: list[dict[str, Any]] = []
        else:
            krs = _rows(
                conn.execute(
                    f"SELECT id, {', '.join(KR_FIELDS)} FROM okr_key_results WHERE objective_id = ANY(%s)",
                    (ids,),
                )
            )
            checkins = _rows(
                conn.execute(
                    "SELECT id, kr_id, value, confidence, note, author, created_at FROM okr_checkins "
                    "WHERE kr_id = ANY(%s) ORDER BY created_at DESC",
                    ([k["id"] for k in krs],),
                )
            )
        cfr = _rows(
            conn.execute(
                "SELECT id, kind, rep_id, objective_id, text, author, created_at FROM okr_cfr "
                "WHERE org_id = %s AND created_at >= %s ORDER BY created_at DESC LIMIT 200",
                (org, datetime(year, 1, 1)),
            )
        )
    for kr in krs:
        kr["start_value"] = _num(kr["start_value"])
        kr["target_value"] = _num(kr["target_value"])
    for c in checkins:
        c["value"] = _num(c["value"])
    return {"objectives": objectives, "krs": krs, "checkins": checkins, "cfr": cfr}


def objective_row(obj_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        if not _ready(conn):
            return None
        rows = _rows(
            conn.execute(
                f"SELECT id, {', '.join(OBJECTIVE_FIELDS)} FROM okr_objectives WHERE org_id = %s AND id = %s",
                (get_org_id(), obj_id),
            )
        )
    return rows[0] if rows else None


def kr_row(kr_id: int) -> dict[str, Any] | None:
    """Anahtar sonuç ve bağlı olduğu hedefin sahibi."""
    with connect() as conn:
        if not _ready(conn):
            return None
        rows = _rows(
            conn.execute(
                "SELECT k.id, k.objective_id, k.owner_rep_id, o.owner, o.kind FROM okr_key_results k "
                "JOIN okr_objectives o ON o.id = k.objective_id WHERE o.org_id = %s AND k.id = %s",
                (get_org_id(), kr_id),
            )
        )
    return rows[0] if rows else None


def _adapt(value: Any) -> Any:
    return Json(value) if isinstance(value, (list, dict)) else value


def insert(table: str, values: dict[str, Any]) -> int:
    cols = list(values)
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        row = conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            [_adapt(values[c]) for c in cols],
        ).fetchone()
    return int(row[0])


def update(table: str, row_id: int, values: dict[str, Any]) -> None:
    if not values:
        return
    sets = ", ".join(f"{c} = %s" for c in values)
    if table == "okr_objectives":
        sets += ", updated_at = now()"
    with connect() as conn:
        conn.execute(SCHEMA_SQL)  # eski kurulumda phasing sütunu eklenir
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = %s", [*map(_adapt, values.values()), row_id])


def delete(table: str, row_id: int) -> None:
    with connect() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = %s", (row_id,))


def seed_year(year: int, template: list[dict[str, Any]], *, by: str) -> int:
    """Şablonu yıla yükler; o yılda hedef varsa dokunmaz. Eklenen hedef sayısı."""
    org = get_org_id()
    with connect() as conn:
        conn.execute(SCHEMA_SQL)
        exists = conn.execute(
            "SELECT 1 FROM okr_objectives WHERE org_id = %s AND year = %s LIMIT 1", (org, year)
        ).fetchone()
        if exists:
            return 0
        keys: dict[str, int] = {}
        n = 0
        for sort, obj in enumerate(template):
            parent = keys.get(obj.get("parent") or "")
            row = conn.execute(
                "INSERT INTO okr_objectives (org_id, year, quarter, owner, title, description, kind, status, "
                "parent_id, sort, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    org, year, obj["quarter"], TEAM, obj["title"], obj.get("description"),
                    obj["kind"], obj["status"], parent, sort, by,
                ),
            ).fetchone()
            obj_id = int(row[0])
            if obj.get("key"):
                keys[obj["key"]] = obj_id
            for kr_sort, kr in enumerate(obj["krs"]):
                conn.execute(
                    "INSERT INTO okr_key_results (objective_id, title, metric, unit, direction, start_value, "
                    "target_value, phasing, sort) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        obj_id, kr["title"], kr.get("metric"), kr.get("unit", "sayi"),
                        kr.get("direction", "up"), kr.get("start", 0), kr["target"],
                        _adapt(kr.get("phasing")), kr_sort,
                    ),
                )
            n += 1
    return n
