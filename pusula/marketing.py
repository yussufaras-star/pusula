"""Pazarlama ekibine öneriler: ciro kartıyla aynı iki dönem, lead kanalı gözüyle.

Kaynak lead_channels.load_leads: lead Zoho'da oluşturulduğu gün sayılır; satış =
lead'in kişi kaydına bağlı, lead'den sonra kapanan kazanılmış deal. Yeni
lead'lerin satışı henüz oturmadığından kanal dönüşümü iki dönem birlikte
okunur.

Üç bölüm:
- fiyat: yeni yıl fiyat artışı yaklaşırken bekleyen havuz ve geçen yılın
  Aralık → Ocak cirosu (PRICE_INCREASE'den PRICE_WINDOW gün önce görünür).
- kanal: lead adedi değişen ve satışa dönmeyen / en iyi dönen kanallar.
- veri: kaybolan form kaynağı, UTM'siz lead payı, hatalı numara.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from pusula.config import get_org_id
from pusula.lead_channels import NO_UTM, Lead, channel, form, load_leads
from pusula.lead_reach import quality_bucket
from pusula.panel_data import connect
from pusula.sales_bridge import MONTHS, tl_short
from pusula.sales_report import _WON_DAY
from pusula.sifir_satis import WON_STAGE

Range = tuple[date, date]

PRICE_INCREASE = date(2027, 1, 1)
PRICE_WINDOW = 60  # gün: artıştan bu kadar önce fiyat bölümü görünür
POOL_DAYS = 120  # bekleyen havuz: son bu kadar günde gelen lead'ler
POOL_STATUSES = ("Düşünmek İstiyor", "Almadı-Şimdi Değil/Erteleme", "Satış Fırsatı")
PRICE_SHOCK = "Almadı-Fiyat Şoku/Bütçe Yetersizliği"

MIN_ROW = 20  # tabloda iki dönem toplamı bundan az lead'li kanal "Diğer"e katılır
DROP_BASE = 50  # lead değişimi için önceki dönemde en az bu kadar lead
LEAD_MOVE = 0.20  # kanal lead'i bu kadar değişirse tespit
MIN_YIELD = 100  # dönüşüm kıyası için iki dönem toplamı en az bu kadar lead
LOW_YIELD = 0.5  # kanal dönüşümü tüm lead dönüşümünün yarısından azsa zayıf
BEST_DROP = -0.15
FORM_LOST = 20  # önceki dönemde bu kadar lead getirip şimdi hiç getirmeyen form
UTM_SHARE = 0.20
WRONG_SHARE = 0.10
MAX_LEAD_FINDINGS = 2
MAX_ADVICE = 12


def _name(k: str) -> str:
    return "Kaynağı belirsiz (UTM'siz)" if k == NO_UTM else k


def _int(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _rate(x: float) -> str:
    return f"%{x * 100:.1f}".replace(".", ",")


def _change(a: int, b: int) -> float | None:
    return (a - b) / b if b else None


def _pct(v: float) -> str:
    return f"{'−' if v < 0 else '+'}%{abs(round(v * 100))}"


def side_stats(leads: list[Lead], first: date, last: date) -> dict[str, Any]:
    """Saf: dönemde gelen lead'ler kanal ve form bazında."""
    shown = [x for x in leads if first <= x.day <= last]
    kanal: dict[str, dict[str, int]] = {}
    for x in shown:
        row = kanal.setdefault(channel(x.utm_source), {"lead": 0, "satis": 0})
        row["lead"] += 1
        row["satis"] += int(x.sold)
    return {
        "lead": len(shown),
        "satis": sum(int(x.sold) for x in shown),
        "hatali": sum(1 for x in shown if quality_bucket(x.status) in ("hatali", "yabanci")),
        "kanal": kanal,
        "form": dict(Counter(form(x.source) for x in shown)),
    }


def channel_rows(sa: dict[str, Any], sb: dict[str, Any]) -> list[dict[str, Any]]:
    """Saf: kanal tablosu, iki dönemde en çok lead gelenden; küçükler "Diğer"de."""
    rows: dict[str, dict[str, Any]] = {}
    for k in set(sa["kanal"]) | set(sb["kanal"]):
        a, b = sa["kanal"].get(k, {}), sb["kanal"].get(k, {})
        la, lb = a.get("lead", 0), b.get("lead", 0)
        key = k if la + lb >= MIN_ROW else "Diğer"
        r = rows.setdefault(key, {"kanal": key, "ad": _name(key), "lead_a": 0, "lead_b": 0, "satis": 0})
        r["lead_a"] += la
        r["lead_b"] += lb
        r["satis"] += a.get("satis", 0) + b.get("satis", 0)
    out = []
    for r in rows.values():
        n = r["lead_a"] + r["lead_b"]
        out.append({**r, "degisim": _change(r["lead_a"], r["lead_b"]), "oran": r["satis"] / n if n else None})
    tail = {"Diğer": 2, NO_UTM: 1}
    out.sort(key=lambda r: (tail.get(r["kanal"], 0), -(r["lead_a"] + r["lead_b"])))
    return out


def _yields(rows: list[dict[str, Any]]) -> tuple[float | None, list[dict[str, Any]]]:
    """Tüm lead dönüşümü ve kıyaslanabilir (bütçesi yönetilebilir) kanallar."""
    leads = sum(r["lead_a"] + r["lead_b"] for r in rows)
    total = sum(r["satis"] for r in rows) / leads if leads else None
    sized = [r for r in rows if r["kanal"] not in (NO_UTM, "Diğer") and r["lead_a"] + r["lead_b"] >= MIN_YIELD]
    return total, sized


def build_marketing(
    sa: dict[str, Any],
    sb: dict[str, Any],
    *,
    today: date,
    pool: dict[str, int] | None = None,
    season: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Saf: pazarlama tespitleri ve önerileri. pool: havuzdaki statü adetleri;
    season: geçen yıl artıştan önceki ve sonraki ayın cirosu."""
    rows = channel_rows(sa, sb)
    total, sized = _yields(rows)
    found: list[dict[str, str]] = []
    tips: list[dict[str, str]] = []

    def note(grup: str, ton: str, metin: str) -> None:
        found.append({"grup": grup, "ton": ton, "metin": metin})

    def tip(grup: str, metin: str) -> None:
        tips.append({"grup": grup, "metin": metin})

    # Fiyat artışı
    days_left = (PRICE_INCREASE - today).days
    if 0 < days_left <= PRICE_WINDOW:
        last_day = PRICE_INCREASE - timedelta(days=1)
        deadline = f"{last_day.day} {MONTHS[last_day.month - 1]}"
        pool = pool or {}
        waiting = sum(pool.get(s, 0) for s in POOL_STATUSES)
        shock = pool.get(PRICE_SHOCK, 0)
        note("fiyat", "neg", f"Yeni yıl fiyat artışına {days_left} gün var; mevcut fiyat {deadline}'a kadar geçerli.")
        tip("fiyat", f"Fiyat artışını şimdi ve net tarihle duyurun (e-posta, WhatsApp, sosyal medya, reklam): "
                     f"“Mevcut fiyat {deadline}'a kadar.” Satış ekibi görüşmelerde aynı mesajı kullansın.")
        if waiting:
            note("fiyat", "neg", f"Son {POOL_DAYS} günde gelip “Düşünmek istiyor”, “Şimdi değil” ya da “Satış "
                                 f"fırsatı”nda bekleyen {_int(waiting)} lead var.")
            tip("fiyat", f"Bekleyen {_int(waiting)} lead'e artış öncesi “son çağrı” dizisi gönderin (WhatsApp + "
                         f"e-posta); dönüş yapanları satış ekibi aynı gün arasın.")
        if shock:
            note("fiyat", "neg", f"Son {POOL_DAYS} günde {_int(shock)} lead fiyat ya da bütçe yüzünden almadı.")
            tip("fiyat", f"Fiyat/bütçe yüzünden almayan {_int(shock)} lead'e artıştan önce taksit ve giriş "
                         f"paketi seçeneğiyle dönün; artış sonrası bu kitleyi kazanmak daha zor.")
        tip("fiyat", "Kamp ve Plus üyelerine Premium'a geçişi artış öncesi fiyattan önerin.")
        if season and season[0] > 0:
            before, after = season
            ch = (after - before) / before
            b_m = MONTHS[(PRICE_INCREASE.month - 2) % 12]
            a_m = MONTHS[PRICE_INCREASE.month - 1]
            note("fiyat", "neg" if ch < 0 else "pos",
                 f"Geçen yıl {b_m} → {a_m} ciro: {tl_short(before)} → {tl_short(after)} ({_pct(ch)}).")
            if ch < 0:
                tip("fiyat", f"{a_m} düşüşüne şimdiden hazırlanın: yeni fiyatın değerini anlatan üye başarı "
                             f"hikâyeleri ve vaka videoları {a_m}'ın ilk haftasında yayında olsun.")

    # Kanallar
    def lead_text(r: dict[str, Any], way: str) -> str:
        who = r["ad"] if r["kanal"] == NO_UTM else f"{r['ad']} kanalından gelen"
        return f"{who} lead {way}: {_int(r['lead_b'])} → {_int(r['lead_a'])} ({_pct(r['degisim'])})."

    moved = [r for r in rows if r["kanal"] != "Diğer" and r["lead_b"] >= DROP_BASE and r["degisim"] is not None]
    drops = sorted((r for r in moved if r["degisim"] <= -LEAD_MOVE), key=lambda r: r["lead_a"] - r["lead_b"])
    gains = sorted((r for r in moved if r["degisim"] >= LEAD_MOVE), key=lambda r: r["lead_b"] - r["lead_a"])
    for r in drops[:MAX_LEAD_FINDINGS]:
        note("kanal", "neg", lead_text(r, "azaldı"))
    for r in gains[:1]:
        note("kanal", "pos", lead_text(r, "arttı"))

    low = best = None
    if total and sized:
        weak = [r for r in sized if r["oran"] < LOW_YIELD * total]
        low = max(weak, key=lambda r: r["lead_a"] + r["lead_b"], default=None)
        best = max(sized, key=lambda r: r["oran"])
        if best["oran"] <= total:
            best = None
    if low:
        n = low["lead_a"] + low["lead_b"]
        note("kanal", "neg", f"{low['ad']}: iki dönemde {_int(n)} lead'den {low['satis']} satış "
                             f"({_rate(low['oran'])}); tüm lead'lerde {_rate(total)}.")
        tip("kanal", f"{low['ad']} çok lead getiriyor ama satışa dönmüyor: forma bütçe ve zaman sorusu ekleyerek "
                     f"niteleyin" + (f", bütçenin bir kısmını {best['ad']} kanalına kaydırmayı deneyin." if best else "."))
    if best:
        n = best["lead_a"] + best["lead_b"]
        note("kanal", "pos", f"En iyi dönen kanal {best['ad']}: {_int(n)} lead'den {best['satis']} satış "
                             f"({_rate(best['oran'])}).")
        if best["degisim"] is not None and best["degisim"] <= BEST_DROP:
            tip("kanal", f"{best['ad']} en iyi dönen kanal ama lead'i %{abs(round(best['degisim'] * 100))} azaldı: içerik ve "
                         f"reklam sıklığını geri getirin.")
        else:
            tip("kanal", f"{best['ad']} en iyi dönen kanal: bütçeyi kademeli artırıp lead başı satışı izleyin.")
    organic = next((r for r in drops if r["kanal"] == NO_UTM), None)
    if organic:
        tip("kanal", "Kaynağı belirsiz (organik, direkt, bülten) lead'ler azaldı: e-posta bülteni, WhatsApp "
                     "yayını ve organik içerik ritmini geri getirin.")

    # Veri
    lost = sorted((k for k, n in sb["form"].items() if n >= FORM_LOST and not sa["form"].get(k)),
                  key=lambda k: -sb["form"][k])
    for k in lost:
        note("veri", "neg", f"“{k}” kaynağıyla gelen lead kesildi: {_int(sb['form'][k])} → 0.")
    if lost:
        names = " ve ".join(f"“{k}”" for k in lost)
        tip("veri", f"{names} kaynağını ve CRM'deki Lead Source etiketini kontrol edin: ya form kapandı ya da "
                    f"etiket düşüyor.")
    no_utm = sa["kanal"].get(NO_UTM, {}).get("lead", 0)
    if sa["lead"] and no_utm / sa["lead"] >= UTM_SHARE:
        note("veri", "neg", f"Kaynağı (UTM) olmayan lead payı %{round(100 * no_utm / sa['lead'])}: bu lead'lerin "
                            f"hangi kanaldan geldiği bilinmiyor.")
        tip("veri", "Tüm reklam, bio, bülten ve WhatsApp linklerine UTM ekleyin; kanal bütçesini doğru "
                    "dağıtmanın ön şartı.")
    if sa["lead"] and sa["hatali"] / sa["lead"] >= WRONG_SHARE:
        note("veri", "neg", f"Hatalı ya da yabancı numaralı lead payı %{round(100 * sa['hatali'] / sa['lead'])}.")
        tip("veri", "Formlara telefon doğrulaması ekleyin (SMS kodu ya da numara biçimi kontrolü); hatalı "
                    "numara satış ekibinin zamanını yiyor.")

    return {
        "lead_a": sa["lead"],
        "lead_b": sb["lead"],
        "lead_degisim": _change(sa["lead"], sb["lead"]),
        "kanallar": rows,
        "tespitler": found,
        "oneriler": tips[:MAX_ADVICE],
    }


def _pool(today: date) -> dict[str, int]:
    since = today - timedelta(days=POOL_DAYS)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT l.status, count(*)::int FROM leads l
            JOIN lead_attribution a ON a.org_id = l.org_id AND a.lead_id = l.lead_id
            WHERE l.org_id = %s AND a.zoho_created_at >= %s AND l.status = ANY(%s)
            GROUP BY 1
            """,
            (get_org_id(), since, [*POOL_STATUSES, PRICE_SHOCK]),
        ).fetchall()
    return {str(s): int(n) for s, n in rows}


def _season() -> tuple[float, float] | None:
    """Geçen yıl artıştan önceki ve artış ayının kazanılan cirosu."""
    after_first = PRICE_INCREASE.replace(year=PRICE_INCREASE.year - 1)
    before_first = (after_first - timedelta(days=1)).replace(day=1)
    after_last = (after_first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {_WON_DAY} < %s, coalesce(sum(amount), 0)::float FROM deal_packages
            WHERE org_id = %s AND stage = %s AND {_WON_DAY} BETWEEN %s AND %s
            GROUP BY 1
            """,
            (after_first, get_org_id(), WON_STAGE, before_first, after_last),
        ).fetchall()
    got = {bool(k): float(v) for k, v in rows}
    if not got.get(True) or not got.get(False):
        return None
    return got[True], got[False]


def load_marketing(a: Range, b: Range, today: date) -> dict[str, Any] | None:
    leads = load_leads(min(a[0], b[0]))
    if leads is None:
        return None
    window = 0 < (PRICE_INCREASE - today).days <= PRICE_WINDOW
    return build_marketing(
        side_stats(leads, *a), side_stats(leads, *b), today=today,
        pool=_pool(today) if window else None, season=_season() if window else None,
    )
