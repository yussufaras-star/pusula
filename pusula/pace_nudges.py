"""Tempo dürtmesi: günlük hedefin gerisinde kalan temsilciye Cliq mesajı.

Sapmayı kod bulur (gaps_for); Claude yalnız bir-iki cümlelik rehberlik
yazar. Yapay zekâ metni içeren mesajlar ilk 14 gün yöneticinin onayından
geçer (panelde "Onay bekleyen dürtmeler"); onaylanmazsa 45 dk sonra düşer.
AI yoksa veya çağrı başarısızsa deterministik mesaj onaysız gider.
Gölge mod (PUSULA_SHADOW_EMAIL) tüm gönderimleri o adrese çevirir.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

from psycopg.types.json import Json

from pusula import llm
from pusula.block_share import apply_shadow, load_sales_reps, post_cliq
from pusula.config import get_org_id
from pusula.metrics import TZ, shift_bounds
from pusula.panel_data import connect
from pusula.targets import METRICS

logger = logging.getLogger(__name__)

PACE_RATIO = 0.8
MIN_BY_NOW = {"arama": 5.0, "ulasilan": 3.0, "randevu": 2.0}
DOLULUK_GAP = 10.0
DOLULUK_MIN_MESAI = 90.0
FIRST_HOUR = 10
LAST_MINUTES = 30
COOLDOWN_MIN = 120
MAX_PER_DAY = 3
STALE_MIN = 20
PENDING_TTL_MIN = 45
APPROVAL_DAYS = 14
SKIP_STATUSES = {"izinli", "mesai_disi", "baslamadi", "gecmis"}
_LOCK_KEY = 4_204_210

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nudge_queue (
    id          bigserial PRIMARY KEY,
    org_id      text NOT NULL DEFAULT 'rexven',
    rep_id      text NOT NULL,
    rep_name    text,
    day         date NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    kind        text NOT NULL DEFAULT 'tempo',
    facts       jsonb,
    body        text NOT NULL,
    ai_text     text,
    final_text  text,
    status      text NOT NULL,
    decided_by  text,
    decided_at  timestamptz,
    sent_at     timestamptz,
    failure     text,
    shadow      boolean
);
CREATE INDEX IF NOT EXISTS nudge_queue_day ON nudge_queue (org_id, day);
"""

AI_SYSTEM = """Sen Rexven telesatış ekibinin operasyon koçusun. Bir temsilciye \
Zoho Cliq'ten gidecek kısa tempo uyarısının sonuna eklenecek rehberlik \
cümlesini yazıyorsun. Uyarının sayısal kısmı zaten mesajda var; sen onu \
tekrarlama.

Kurallar:
- En fazla iki kısa cümle, "sen" diliyle, samimi ve saygılı.
- Suçlama, tehdit, başkalarıyla kıyaslama yok.
- Yalnız verilen verilere dayan; sayı veya olay uydurma.
- Önümüzdeki bir saat için tek ve somut bir adım öner (ör. kayıp zaman \
uzunsa arama bloğuna dön, toplantı sonucu girilmemişse önce onu gir, \
ulaşma düşükse farklı saat dilimindeki leadleri dene).
- Selamlama, imza, emoji, madde işareti yok. Yalnız cümleleri yaz."""


# --- saf kurallar --------------------------------------------------------------


def gaps_for(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Hedefin gerisindeki başlıklar. Küçük beklentide sayım uyarısı yok."""
    out: list[dict[str, Any]] = []
    today = card.get("today") or {}
    for metric, target in (card.get("target") or {}).items():
        actual = today.get(metric)
        if metric == "doluluk":
            if actual is None or float(today.get("mesai_dk") or 0) < DOLULUK_MIN_MESAI:
                continue
            if actual < target["daily"] - DOLULUK_GAP:
                out.append({"metric": metric, "actual": actual, **target})
            continue
        by_now = float(target.get("by_now") or 0)
        if by_now < MIN_BY_NOW.get(metric, 1.0):
            continue
        if float(actual or 0) < by_now * PACE_RATIO:
            out.append({"metric": metric, "actual": actual or 0, **target})
    return out


def in_window(local: datetime) -> bool:
    """10:00'dan mesai bitimine 30 dk kalana kadar."""
    bounds = shift_bounds(local.date())
    if not bounds or local.hour < FIRST_HOUR:
        return False
    return bounds[0] <= local < bounds[1] - timedelta(minutes=LAST_MINUTES)


def cooldown_ok(sent_times: list[datetime], now: datetime) -> bool:
    if len(sent_times) >= MAX_PER_DAY:
        return False
    return not sent_times or now - max(sent_times) >= timedelta(minutes=COOLDOWN_MIN)


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0f}" if float(value).is_integer() or value >= 10 else f"{value:.1f}"


def _gap_line(gap: dict[str, Any]) -> str:
    label = METRICS[gap["metric"]]
    basis = "günlük ortalaman" if gap["source"] == "ortalama" else "günlük hedef"
    if gap["metric"] == "doluluk":
        return f"- {label}: %{_fmt(gap['actual'])} ({basis} %{_fmt(gap['daily'])})"
    return (
        f"- {label}: {_fmt(gap['actual'])}; bu saate kadar {_fmt(gap['by_now'])} "
        f"bekleniyordu ({basis} {_fmt(gap['daily'])})"
    )


def first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.strip() else full_name


def format_body(name: str, gaps: list[dict[str, Any]], local: datetime) -> str:
    lines = [
        f"🧭 Pusula · {local:%H:%M}",
        f"{first_name(name)}, bugün şu başlıklarda temponun gerisindesin:",
        *(_gap_line(gap) for gap in gaps),
    ]
    bounds = shift_bounds(local.date())
    if bounds:
        left = int((bounds[1] - local).total_seconds() // 60)
        lines.append(f"Mesai bitimine {left // 60} sa {left % 60} dk var; toparlamak için vakit var.")
    return "\n".join(lines)


def compose(body: str, ai_text: str | None) -> str:
    return f"{body}\n💡 {ai_text}" if ai_text else body


def ai_facts(card: dict[str, Any], gaps: list[dict[str, Any]], local: datetime) -> dict[str, Any]:
    """Modele giden veri: temsilcinin kendi sayıları. Müşteri bilgisi yok."""
    today = card.get("today") or {}
    bounds = shift_bounds(local.date())
    return {
        "saat": f"{local:%H:%M}",
        "mesai_bitimine_dk": int((bounds[1] - local).total_seconds() // 60) if bounds else None,
        "geride_kalanlar": [
            {
                "baslik": METRICS[g["metric"]],
                "gerceklesen": g["actual"],
                "bu_saate_kadar_beklenen": g["by_now"],
                "gunluk_hedef": g["daily"],
                "hedef_kaynagi": "gecmis_ortalama" if g["source"] == "ortalama" else "yonetici",
            }
            for g in gaps
        ],
        "durum": card.get("status"),
        "hareketsiz_dk": card.get("idle_min"),
        "kayip_zaman_dk": today.get("kayip_dk"),
        "en_uzun_bosluklar_dk": [g.get("minutes") for g in (card.get("gaps") or [])[:3]],
        "ulasma_orani": today.get("ulasma_orani"),
        "ort_gorusme_sn": today.get("sure_ort"),
        "sonucu_girilmemis_toplanti": today.get("sonuc_girilmedi"),
        "whatsapp": today.get("whatsapp"),
    }


def guidance(facts: dict[str, Any]) -> str | None:
    return llm.complete(
        AI_SYSTEM,
        "Temsilcinin bugünkü verisi:\n" + json.dumps(facts, ensure_ascii=False, default=str),
        max_tokens=1500,
    )


# --- kuyruk ------------------------------------------------------------------------


def _ensure(conn: Any) -> None:
    conn.execute(SCHEMA_SQL)


def _expire(conn: Any, now: datetime) -> None:
    conn.execute(
        """
        UPDATE nudge_queue SET status = 'suresi_doldu', decided_at = %s
        WHERE org_id = %s AND status = 'bekliyor' AND created_at < %s
        """,
        (now, get_org_id(), now - timedelta(minutes=PENDING_TTL_MIN)),
    )


def approval_required(conn: Any, now: datetime) -> bool:
    """İlk onaylı AI mesajından 14 gün sonra onay kalkar. Env ile zorlanabilir."""
    forced = os.environ.get("PUSULA_AI_APPROVAL", "").strip()
    if forced in ("0", "1"):
        return forced == "1"
    row = conn.execute(
        """
        SELECT min(decided_at) FROM nudge_queue
        WHERE org_id = %s AND ai_text IS NOT NULL AND status = 'gonderildi'
          AND decided_by IS NOT NULL
        """,
        (get_org_id(),),
    ).fetchone()
    first = row[0] if row else None
    return first is None or now < first + timedelta(days=APPROVAL_DAYS)


def _send(conn: Any, row_id: int, text: str, *, name: str, email: str, now: datetime) -> bool:
    shadowed, destination, is_shadow = apply_shadow(text, name)
    try:
        if not (destination if is_shadow else email):
            raise RuntimeError("Cliq alıcısı (e-posta) yok")
        post_cliq(shadowed, destination if is_shadow else email)
    except Exception as exc:  # noqa: BLE001 — hata kuyrukta görünür
        failure = f"{type(exc).__name__}: {exc}"[:500]
        conn.execute(
            "UPDATE nudge_queue SET status = 'hata', failure = %s, final_text = %s WHERE id = %s",
            (failure, text, row_id),
        )
        logger.warning("tempo dürtmesi gönderilemedi: %s", failure)
        return False
    conn.execute(
        """
        UPDATE nudge_queue SET status = 'gonderildi', final_text = %s, sent_at = %s,
               shadow = %s, failure = NULL
        WHERE id = %s
        """,
        (text, now, is_shadow, row_id),
    )
    return True


def _today_times(conn: Any, day: date) -> dict[str, list[datetime]]:
    rows = conn.execute(
        """
        SELECT rep_id, created_at FROM nudge_queue
        WHERE org_id = %s AND day = %s AND status <> 'hata'
        """,
        (get_org_id(), day),
    ).fetchall()
    out: dict[str, list[datetime]] = {}
    for rep_id, created_at in rows:
        out.setdefault(str(rep_id), []).append(created_at)
    return out


def run_pace_nudges(now: datetime | None = None, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Saatlik ingest sonrası ve cron'dan çağrılır. Aynı anda tek çalışma."""
    from pusula.live import load_live

    now = (now or datetime.now(TZ)).astimezone(TZ)
    if not in_window(now):
        return {"skipped": "zaman_disi", "at": now.isoformat()}
    payload = payload or load_live(now.date(), now)
    as_of = payload.get("data_as_of")
    if as_of is None or now - as_of > timedelta(minutes=STALE_MIN):
        return {"skipped": "veri_bayat", "data_as_of": as_of, "at": now.isoformat()}

    emails = {rep.rep_id: rep.email for rep in load_sales_reps()}
    report: dict[str, Any] = {"at": now.isoformat(), "queued": 0, "sent": 0, "failed": 0, "behind": 0}
    # İşlem düzeyi kilit ayrı bağlantıda: transaction pooler'da da güvenli.
    with connect() as lock_conn, connect() as conn:
        locked = lock_conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (_LOCK_KEY,)).fetchone()
        if not locked or not locked[0]:
            return {"skipped": "baska_calisma_suruyor", "at": now.isoformat()}
        _ensure(conn)
        _expire(conn, now)
        conn.commit()
        needs_approval = approval_required(conn, now)
        times = _today_times(conn, now.date())
        conn.commit()  # AI çağrısı sırasında açık işlem kalmasın
        for card in payload["reps"]:
            if card.get("status") in SKIP_STATUSES or card["rep_id"] not in emails:
                continue
            gaps = gaps_for(card)
            if not gaps:
                continue
            report["behind"] += 1
            if not cooldown_ok(times.get(card["rep_id"], []), now):
                continue
            facts = ai_facts(card, gaps, now)
            body = format_body(card["name"], gaps, now)
            ai_text = guidance(facts)
            pending = bool(ai_text) and needs_approval
            row = conn.execute(
                """
                INSERT INTO nudge_queue (org_id, rep_id, rep_name, day, created_at,
                                         facts, body, ai_text, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    get_org_id(), card["rep_id"], card["name"], now.date(), now,
                    Json(facts), body, ai_text, "bekliyor" if pending else "gonderiliyor",
                ),
            ).fetchone()
            conn.commit()
            if pending:
                report["queued"] += 1
                continue
            ok = _send(conn, row[0], compose(body, ai_text), name=card["name"],
                       email=emails[card["rep_id"]], now=now)
            conn.commit()
            report["sent" if ok else "failed"] += 1
        report["approval_required"] = needs_approval
        report["ai"] = llm.enabled()
    return report


# --- panel ---------------------------------------------------------------------------


def list_queue(day: date, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with connect() as conn:
        exists = conn.execute("SELECT to_regclass('nudge_queue')").fetchone()
        if not exists or exists[0] is None:
            return {"items": [], "approval_required": True, "ai": llm.enabled()}
        _expire(conn, now)
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, rep_id, rep_name, created_at, body, ai_text, final_text, status,
                   decided_by, decided_at, sent_at, failure, shadow
            FROM nudge_queue WHERE org_id = %s AND day = %s
            ORDER BY created_at DESC
            """,
            (get_org_id(), day),
        ).fetchall()
        needs_approval = approval_required(conn, now)
    keys = ("id", "rep_id", "rep_name", "created_at", "body", "ai_text", "final_text", "status",
            "decided_by", "decided_at", "sent_at", "failure", "shadow")
    items = []
    for row in rows:
        item = dict(zip(keys, row))
        item["text"] = item["final_text"] or compose(item["body"], item["ai_text"])
        items.append(item)
    return {"items": items, "approval_required": needs_approval, "ai": llm.enabled()}


class QueueError(Exception):
    pass


def decide(row_id: int, *, approve: bool, text: str | None, by: str, now: datetime | None = None) -> str:
    """Bekleyen mesajı onaylayıp gönderir veya reddeder. Yeni durumu döner."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    with connect() as conn:
        _expire(conn, now)
        row = conn.execute(
            """
            UPDATE nudge_queue SET status = %s, decided_by = %s, decided_at = %s
            WHERE id = %s AND org_id = %s AND status = 'bekliyor'
            RETURNING rep_id, rep_name, body, ai_text
            """,
            ("gonderiliyor" if approve else "reddedildi", by, now, row_id, get_org_id()),
        ).fetchone()
        conn.commit()
        if row is None:
            raise QueueError("Mesaj artık onay beklemiyor (süresi dolmuş veya karar verilmiş)")
        if not approve:
            return "reddedildi"
        rep_id, name, body, ai_text = row
        emails = {rep.rep_id: rep.email for rep in load_sales_reps()}
        final = (text or "").strip() or compose(body, ai_text)
        ok = _send(conn, row_id, final, name=str(name), email=emails.get(str(rep_id), ""), now=now)
        conn.commit()
    return "gonderildi" if ok else "hata"
