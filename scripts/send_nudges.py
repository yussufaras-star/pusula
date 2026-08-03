"""Günlük dürtü üretir ve Cliq'e gölge modda gönderir.

Üç sinyal (temsilci başına toplam en fazla 3):
  1. pencere_aciliyor — active lead, outbound < 3
  2. kayip_randevu    — Randevu Alındı var, Sunum Yok, sonrası temas yok
  3. gecikmis_taahhut — broken commitment, due_at son 14 gün
     (sahiplik: thread → leads.owner_rep_id)

Kontenjan: tip başına sabit kota yok. Toplam 3 slot, uygun aday
sayısına göre orantılı (largest remainder) dağıtılır.

Haftanın ilk iş gününde (Pazartesi, Europe/Istanbul) ilgili
bölümün altına sayı içeren kanıt eklenir. Aynı kanıt aynı
temsilciye 8 hafta içinde tekrar gitmez; evidence_id
nudges.payload'a yazılır.

"Bu hafta" bölümü: rep_snapshots bu hafta vs geçen hafta.
Her iki haftada da satır yoksa bölüm basılmaz.

Gölge mod: --apply olsa bile tüm DM'ler PUSULA_SHADOW_EMAIL'e gider;
gerçek temsilciye gitmez. Mesajda hangi temsilci için üretildiği yazar.

Kullanım:
    python scripts/send_nudges.py
    python scripts/send_nudges.py --apply

Varsayılan dry-run. DATABASE_URL_POOLED gerekir (pgbouncer 6543);
--apply için ayrıca CLIQ_WEBHOOK_URL ve PUSULA_SHADOW_EMAIL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from psycopg.types.json import Json
from dotenv import load_dotenv

from pusula.config import get_org_id

_TZ = ZoneInfo("Europe/Istanbul")
_MAX_PER_REP = 3
_TYPE_ORDER = ("pencere_aciliyor", "kayip_randevu", "gecikmis_taahhut")
_TYPE_TITLE = {
    "pencere_aciliyor": "Pencere açılıyor",
    "kayip_randevu": "Kayıp randevu",
    "gecikmis_taahhut": "Gecikmiş taahhüt",
}
_SNAPSHOT_LABELS = (
    ("bekleyen_lead", "Bekleyen lead"),
    ("kayip_randevu", "Kayıp randevu"),
    ("acik_taahhut", "Açık taahhüt"),
    ("tutulan_hafta", "Tutulan"),
    ("bozulan_hafta", "Bozulan"),
)

EvidenceTip = Literal["ekip", "kisisel", "sistem"]


@dataclass(frozen=True)
class Evidence:
    id: str
    metin: str
    tip: EvidenceTip
    aktif: bool = True


# Kanıt havuzu (ileride evidence tablosuna taşınabilir).
_EVIDENCE_POOL: tuple[Evidence, ...] = (
    Evidence(
        "ev_ekip_01",
        "Randevu sonrası aynı gün temas kurulan hatlarda sunuma geçiş daha sıktı.",
        "ekip",
    ),
    Evidence(
        "ev_ekip_02",
        "48 saat penceresinde üçüncü aramaya kalan lead'lerin çoğu arşive kaydı.",
        "ekip",
    ),
    Evidence(
        "ev_ekip_03",
        "İlk iki aramada ulaşılamayanlarda üçüncü deneme çoğu zaman yapılmadı.",
        "ekip",
    ),
    Evidence(
        "ev_kisisel_01",
        "Konu netleşmeden fiyat açılan görüşmelerde görüşme süresi kısalıyordu.",
        "kisisel",
    ),
    Evidence(
        "ev_kisisel_02",
        "Taahhüt verilen ama vadesi geçen kayıtlarda sonraki adım çoğu zaman boş kaldı.",
        "kisisel",
    ),
    Evidence(
        "ev_sistem_01",
        "Aging'e düşen lead'lerde ortalama ilk anlamlı temas birkaç gün gecikti.",
        "sistem",
    ),
    Evidence(
        "ev_sistem_02",
        "Kayıp randevu adaylarında sunum kaydı olmadan hat soğumuş görünüyordu.",
        "sistem",
    ),
)

_TIP_TO_TYPE: dict[EvidenceTip, str] = {
    "ekip": "kayip_randevu",
    "kisisel": "gecikmis_taahhut",
    "sistem": "pencere_aciliyor",
}

_USED_EVIDENCE_SQL = """
    SELECT DISTINCT payload->>'evidence_id'
    FROM nudges
    WHERE org_id = %s
      AND rep_id = %s
      AND sent_at >= now() - interval '8 weeks'
      AND payload->>'evidence_id' IS NOT NULL
"""

_PENCERE_SQL = """
    SELECT
        l.owner_rep_id AS rep_id,
        l.thread_id,
        l.lead_id,
        NULL::bigint AS commitment_id,
        'pencere_aciliyor'::text AS nudge_type,
        public.pazarsiz_saat(
            coalesce(l.assigned_at, l.created_at), now()
        ) AS sort_key,
        (
            SELECT count(*)::int FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
        ) AS outbound_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = l.org_id
              AND i.thread_id = l.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        l.full_name AS contact_name
    FROM leads l
    WHERE l.org_id = %s
      AND l.pusula_state = 'active'
      AND l.owner_rep_id IS NOT NULL
      AND l.thread_id IS NOT NULL
      AND (
            SELECT count(*)::int FROM events e
            WHERE e.org_id = l.org_id
              AND e.thread_id = l.thread_id
              AND e.channel = 'call'
              AND e.direction = 'outbound'
              AND coalesce(e.meta->>'scheduled', 'false') <> 'true'
              AND e.occurred_at >= coalesce(l.assigned_at, l.created_at)
      ) < 3
    ORDER BY sort_key DESC NULLS LAST
"""

_KAYIP_SQL = """
    WITH randevu AS (
        SELECT
            e.org_id,
            e.thread_id,
            e.rep_id,
            e.occurred_at AS randevu_at,
            row_number() OVER (
                PARTITION BY e.org_id, e.thread_id
                ORDER BY e.occurred_at DESC
            ) AS rn
        FROM events e
        WHERE e.org_id = %s
          AND (
              e.meta->>'outcome_key' = 'meeting_booked'
              OR e.meta->>'call_result' = 'Randevu Alındı'
          )
    )
    SELECT
        coalesce(r.rep_id, t.owner_rep_id) AS rep_id,
        r.thread_id,
        (
            SELECT l.lead_id FROM leads l
            WHERE l.org_id = r.org_id AND l.thread_id = r.thread_id
            ORDER BY l.assigned_at DESC NULLS LAST
            LIMIT 1
        ) AS lead_id,
        NULL::bigint AS commitment_id,
        'kayip_randevu'::text AS nudge_type,
        extract(epoch FROM r.randevu_at) AS sort_key,
        NULL::int AS outbound_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = r.org_id
              AND i.thread_id = r.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        r.randevu_at,
        (
            SELECT coalesce(
                (
                    SELECT l.full_name FROM leads l
                    WHERE l.org_id = r.org_id AND l.thread_id = r.thread_id
                      AND l.full_name IS NOT NULL
                    ORDER BY l.assigned_at DESC NULLS LAST
                    LIMIT 1
                ),
                (
                    SELECT ct.full_name FROM contacts ct
                    WHERE ct.org_id = r.org_id AND ct.thread_id = r.thread_id
                      AND ct.full_name IS NOT NULL
                    ORDER BY ct.created_at DESC NULLS LAST
                    LIMIT 1
                )
            )
        ) AS contact_name
    FROM randevu r
    JOIN threads t
      ON t.org_id = r.org_id AND t.thread_id = r.thread_id
    WHERE r.rn = 1
      AND coalesce(r.rep_id, t.owner_rep_id) IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM events e2
          WHERE e2.org_id = r.org_id
            AND e2.thread_id = r.thread_id
            AND (
                e2.meta->>'outcome_key' = 'demo_done'
                OR e2.meta->>'call_result' = 'Sunum Yapıldı'
            )
      )
      AND NOT EXISTS (
          SELECT 1 FROM events e3
          WHERE e3.org_id = r.org_id
            AND e3.thread_id = r.thread_id
            AND e3.occurred_at > r.randevu_at
            AND coalesce(e3.meta->>'scheduled', 'false') <> 'true'
            AND (
                (e3.channel = 'call' AND e3.direction = 'inbound')
                OR e3.channel = 'meeting'
                OR (
                    e3.channel = 'call'
                    AND coalesce(e3.meta->>'outcome_key', '')
                        NOT IN ('', 'no_answer', 'invalid_number')
                )
            )
      )
    ORDER BY r.randevu_at DESC
"""

# Sahiplik: thread → leads.owner_rep_id (event/thread owner değil).
_TAAHHUT_SQL = """
    SELECT
        l.owner_rep_id AS rep_id,
        c.thread_id,
        l.lead_id,
        c.id AS commitment_id,
        'gecikmis_taahhut'::text AS nudge_type,
        extract(epoch FROM c.due_at) AS sort_key,
        NULL::int AS outbound_calls,
        (
            SELECT i.id_value FROM identities i
            WHERE i.org_id = c.org_id
              AND i.thread_id = c.thread_id
              AND i.id_type = 'phone'
            ORDER BY i.id_value
            LIMIT 1
        ) AS phone,
        c.due_at,
        c.text AS commitment_text,
        l.full_name AS contact_name
    FROM commitments c
    JOIN LATERAL (
        SELECT lead_id, owner_rep_id, full_name
        FROM leads
        WHERE org_id = c.org_id AND thread_id = c.thread_id
        ORDER BY assigned_at DESC NULLS LAST
        LIMIT 1
    ) l ON true
    WHERE c.org_id = %s
      AND c.status = 'broken'
      AND c.due_at IS NOT NULL
      AND c.due_at >= now() - interval '14 days'
      AND c.due_at < now()
      AND c.thread_id IS NOT NULL
      AND l.owner_rep_id IS NOT NULL
    ORDER BY c.due_at DESC
"""

_DEDUP_SQL = """
    SELECT 1
    FROM nudges n
    WHERE n.org_id = %s
      AND n.rep_id = %s
      AND n.nudge_type = %s
      AND n.thread_id = %s
      AND n.sent_at >= now() - interval '3 days'
    LIMIT 1
"""

_WEEK_SNAPSHOT_SQL = """
    SELECT
        snapshot_date,
        bekleyen_lead,
        kayip_randevu,
        acik_taahhut,
        tutulan_hafta,
        bozulan_hafta
    FROM rep_snapshots
    WHERE rep_id = %s
      AND snapshot_date >= %s
      AND snapshot_date < %s
    ORDER BY snapshot_date DESC
    LIMIT 1
"""


@dataclass
class NudgeCandidate:
    rep_id: str
    thread_id: str
    lead_id: str | None
    commitment_id: int | None
    nudge_type: str
    sort_key: float
    phone: str | None
    contact_name: str | None = None
    outbound_calls: int | None = None
    randevu_at: datetime | None = None
    due_at: datetime | None = None
    commitment_text: str | None = None


@dataclass(frozen=True)
class WeekSnapshotPair:
    this_date: date
    last_date: date
    this_vals: dict[str, int]
    last_vals: dict[str, int]


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "?"
    local = value.astimezone(_TZ) if value.tzinfo else value.replace(tzinfo=_TZ)
    months = (
        "Oca", "Şub", "Mar", "Nis", "May", "Haz",
        "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara",
    )
    return f"{local.day} {months[local.month - 1]}"


def _fmt_hours(value: Any) -> str:
    if value is None:
        return "?"
    hours = float(value)
    if hours >= 48:
        days = hours / 24.0
        return f"{days:.0f} gün"
    return f"{hours:.0f} saat"


def _identity_label(n: NudgeCandidate) -> str:
    """İsim - telefon; isim yoksa sadece telefon (satır atlanmaz)."""
    phone = n.phone or "telefon yok"
    name = (n.contact_name or "").strip()
    if name:
        return f"{name} - {phone}"
    return phone


def _line_for(n: NudgeCandidate) -> str:
    who = _identity_label(n)
    if n.nudge_type == "pencere_aciliyor":
        calls = n.outbound_calls if n.outbound_calls is not None else 0
        return (
            f"{who} — pazarsız {_fmt_hours(n.sort_key)}, "
            f"{calls} arama"
        )
    if n.nudge_type == "kayip_randevu":
        return f"{who} — randevu {_fmt_dt(n.randevu_at)}, sonrası temas yok"
    due = _fmt_dt(n.due_at)
    note = (n.commitment_text or "").strip()
    if note:
        note = note.replace("\n", " ")
        if len(note) > 60:
            note = note[:57] + "..."
        return f"{who} — vade {due}: {note}"
    return f"{who} — vade {due}"


def _is_first_business_day(now: datetime | None = None) -> bool:
    """Haftanın ilk iş günü: Pazartesi (Europe/Istanbul)."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    return local.weekday() == 0  # Pazartesi


def _pick_evidence(
    conn: psycopg.Connection,
    org_id: str,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> Evidence | None:
    """İlk iş gününde, 8 haftada kullanılmamış bir kanıt seç."""
    if not _is_first_business_day(now):
        return None
    used = {
        str(r[0])
        for r in conn.execute(_USED_EVIDENCE_SQL, (org_id, rep_id)).fetchall()
        if r[0]
    }
    pool = [e for e in _EVIDENCE_POOL if e.aktif and e.id not in used]
    if not pool:
        return None
    local = (now or datetime.now(_TZ)).astimezone(_TZ)
    year, week, _ = local.isocalendar()
    digest = hashlib.sha256(f"{rep_id}:{year}:{week}".encode()).hexdigest()
    return pool[int(digest[:8], 16) % len(pool)]


def _evidence_section(
    evidence: Evidence,
    suitable: dict[str, int],
) -> tuple[str, str] | None:
    """Kanıtın altına yazılacağı tip + sayı içeren cümle."""
    preferred = _TIP_TO_TYPE.get(evidence.tip)
    if preferred and suitable.get(preferred, 0) > 0:
        nudge_type = preferred
    else:
        nudge_type = max(_TYPE_ORDER, key=lambda t: suitable.get(t, 0))
        if suitable.get(nudge_type, 0) <= 0:
            return None
    count = suitable[nudge_type]
    if nudge_type == "gecikmis_taahhut":
        line = f"Son 14 günde {count} gecikmiş taahhüt. {evidence.metin}"
    elif nudge_type == "kayip_randevu":
        line = f"Şu an {count} kayıp randevu adayı. {evidence.metin}"
    else:
        line = f"Penceresi açık {count} lead. {evidence.metin}"
    return nudge_type, line


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else full_name


def _row_to_snapshot_vals(row: tuple[Any, ...]) -> dict[str, int]:
    # snapshot_date, bekleyen_lead, kayip_randevu, acik_taahhut,
    # tutulan_hafta, bozulan_hafta
    keys = [k for k, _ in _SNAPSHOT_LABELS]
    return {k: int(row[i + 1] or 0) for i, k in enumerate(keys)}


def _load_week_compare(
    conn: psycopg.Connection,
    rep_id: str,
    *,
    now: datetime | None = None,
) -> WeekSnapshotPair | None:
    """Bu hafta ve geçen hafta için en son snapshot; ikisi de yoksa None."""
    local = now or datetime.now(_TZ)
    if local.tzinfo is None:
        local = local.replace(tzinfo=_TZ)
    else:
        local = local.astimezone(_TZ)
    today = local.date()
    this_monday = today - timedelta(days=today.weekday())
    next_monday = this_monday + timedelta(days=7)
    last_monday = this_monday - timedelta(days=7)

    this_row = conn.execute(
        _WEEK_SNAPSHOT_SQL, (rep_id, this_monday, next_monday)
    ).fetchone()
    last_row = conn.execute(
        _WEEK_SNAPSHOT_SQL, (rep_id, last_monday, this_monday)
    ).fetchone()
    if this_row is None or last_row is None:
        return None
    return WeekSnapshotPair(
        this_date=this_row[0],
        last_date=last_row[0],
        this_vals=_row_to_snapshot_vals(this_row),
        last_vals=_row_to_snapshot_vals(last_row),
    )


def _build_message(
    *,
    rep_name: str,
    rep_email: str | None,
    selected: list[NudgeCandidate],
    stock: int,
    suitable: dict[str, int],
    evidence: Evidence | None = None,
    week: WeekSnapshotPair | None = None,
) -> str:
    who = rep_name
    if rep_email:
        who = f"{rep_name} <{rep_email}>"
    parts: list[str] = [
        f"[gölge] {who} için",
        "",
        f"Günaydın {_first_name(rep_name)}",
        "",
        "Bugün",
        "",
    ]
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in selected:
        by_type[n.nudge_type].append(n)

    evidence_block = (
        _evidence_section(evidence, suitable) if evidence is not None else None
    )
    evidence_type = evidence_block[0] if evidence_block else None
    evidence_line = evidence_block[1] if evidence_block else None

    for nudge_type in _TYPE_ORDER:
        items = by_type.get(nudge_type) or []
        if not items and nudge_type != evidence_type:
            continue
        if items:
            parts.append(_TYPE_TITLE[nudge_type])
            for item in items:
                parts.append(_line_for(item))
            if evidence_type == nudge_type and evidence_line:
                parts.append(evidence_line)
            parts.append("")
        elif evidence_type == nudge_type and evidence_line:
            parts.append(_TYPE_TITLE[nudge_type])
            parts.append(evidence_line)
            parts.append("")

    if week is not None:
        parts.append("Bu hafta")
        for key, label in _SNAPSHOT_LABELS:
            this_v = week.this_vals.get(key, 0)
            last_v = week.last_vals.get(key, 0)
            parts.append(f"{label}: {this_v} (geçen {last_v})")
        parts.append("")

    parts.append(f"Bekleyen stok: {stock}")
    return "\n".join(parts).rstrip() + "\n"


def _is_dup(conn: psycopg.Connection, org_id: str, n: NudgeCandidate) -> bool:
    row = conn.execute(
        _DEDUP_SQL, (org_id, n.rep_id, n.nudge_type, n.thread_id)
    ).fetchone()
    return row is not None


def _load_candidates(conn: psycopg.Connection, org_id: str) -> list[NudgeCandidate]:
    out: list[NudgeCandidate] = []

    for row in conn.execute(_PENCERE_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, phone, contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                outbound_calls=int(outbound_calls or 0),
            )
        )

    for row in conn.execute(_KAYIP_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, phone, randevu_at, contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                randevu_at=randevu_at,
            )
        )

    for row in conn.execute(_TAAHHUT_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, phone, due_at, commitment_text,
            contact_name,
        ) = row
        out.append(
            NudgeCandidate(
                rep_id=str(rep_id),
                thread_id=str(thread_id),
                lead_id=str(lead_id) if lead_id else None,
                commitment_id=int(commitment_id) if commitment_id is not None else None,
                nudge_type=str(nudge_type),
                sort_key=float(sort_key or 0),
                phone=str(phone) if phone else None,
                contact_name=str(contact_name) if contact_name else None,
                due_at=due_at,
                commitment_text=str(commitment_text) if commitment_text else None,
            )
        )

    return out


def _allocate_slots(counts: dict[str, int], max_slots: int) -> dict[str, int]:
    """Uygun sayıya orantılı kota (largest remainder); tip başına sabit yok."""
    present = [(t, counts.get(t, 0)) for t in _TYPE_ORDER if counts.get(t, 0) > 0]
    total = sum(c for _, c in present)
    if total == 0:
        return {t: 0 for t in _TYPE_ORDER}
    max_slots = min(max_slots, total)
    ideals = [(t, c, max_slots * c / total) for t, c in present]
    floors = {t: min(c, int(ideal)) for t, c, ideal in ideals}
    leftover = max_slots - sum(floors.values())
    remainders = sorted(
        (
            (ideal - int(ideal), -_TYPE_ORDER.index(t), t)
            for t, c, ideal in ideals
        ),
        reverse=True,
    )
    avail = {t: c for t, c in present}
    for _, _, t in remainders:
        if leftover <= 0:
            break
        if floors[t] < avail[t]:
            floors[t] += 1
            leftover -= 1
    return {t: floors.get(t, 0) for t in _TYPE_ORDER}


def _select_for_rep(
    items: list[NudgeCandidate],
) -> tuple[list[NudgeCandidate], dict[str, int], dict[str, int], int]:
    """Orantılı dağılım; (selected, suitable, shown, stock)."""
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in items:
        by_type[n.nudge_type].append(n)
    for nudge_type in _TYPE_ORDER:
        by_type[nudge_type].sort(key=lambda x: x.sort_key, reverse=True)

    suitable = {t: len(by_type[t]) for t in _TYPE_ORDER}
    slots = _allocate_slots(suitable, _MAX_PER_REP)

    selected: list[NudgeCandidate] = []
    shown = {t: 0 for t in _TYPE_ORDER}
    for nudge_type in _TYPE_ORDER:
        take = slots[nudge_type]
        for n in by_type[nudge_type][:take]:
            selected.append(n)
            shown[nudge_type] += 1

    stock = max(0, len(items) - len(selected))
    return selected, suitable, shown, stock


def _post_cliq(webhook_url: str, text: str, userids: str) -> None:
    body = json.dumps(
        {"type": "message", "text": text, "userids": userids},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _insert_nudge(
    conn: psycopg.Connection,
    *,
    org_id: str,
    n: NudgeCandidate,
    payload: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO nudges (
            org_id, rep_id, nudge_type, thread_id, lead_id,
            commitment_id, sent_at, channel, payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, now(), 'cliq', %s)
        """,
        (
            org_id,
            n.rep_id,
            n.nudge_type,
            n.thread_id,
            n.lead_id,
            n.commitment_id,
            Json(payload),
        ),
    )
    return int(cur.rowcount)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Günlük dürtü üretir; gölge modda Cliq'e gönderir."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Cliq'e gönder ve nudges'a yaz (varsayılan: dry-run)",
    )
    args = parser.parse_args()
    dry_run = not args.apply

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL_POOLED")
    if not database_url:
        print("DATABASE_URL_POOLED ortam değişkeni tanımlı değil")
        print("üretilen=0, gönderilen=0, hata=1")
        return 1

    webhook_url = os.environ.get("CLIQ_WEBHOOK_URL")
    shadow_email = os.environ.get("PUSULA_SHADOW_EMAIL")
    if not dry_run:
        missing = [
            name
            for name, val in (
                ("CLIQ_WEBHOOK_URL", webhook_url),
                ("PUSULA_SHADOW_EMAIL", shadow_email),
            )
            if not val
        ]
        if missing:
            print("eksik ortam değişkeni: " + ", ".join(missing))
            print("üretilen=0, gönderilen=0, hata=1")
            return 1

    org_id = get_org_id()
    produced = 0
    sent = 0
    errors = 0

    try:
        with psycopg.connect(database_url, prepare_threshold=None) as conn:
            raw = _load_candidates(conn, org_id)
            named = sum(1 for n in raw if (n.contact_name or "").strip())
            if raw and named == 0:
                print(
                    "uyarı: leads/contacts.full_name boş — "
                    "satırlar sadece telefon; backfill_leads çalıştırın"
                )

            # uygun = dedup öncesi havuz (doğrulama sayılarıyla aynı).
            suitable_raw: dict[str, dict[str, int]] = defaultdict(
                lambda: {t: 0 for t in _TYPE_ORDER}
            )
            for n in raw:
                suitable_raw[n.rep_id][n.nudge_type] += 1

            filtered: list[NudgeCandidate] = []
            skipped_dup = 0
            for n in raw:
                if _is_dup(conn, org_id, n):
                    skipped_dup += 1
                    continue
                filtered.append(n)

            by_rep: dict[str, list[NudgeCandidate]] = defaultdict(list)
            for n in filtered:
                by_rep[n.rep_id].append(n)

            reps = {
                str(r[0]): (str(r[1]), str(r[2]) if r[2] else None)
                for r in conn.execute(
                    """
                    SELECT rep_id, full_name, email
                    FROM reps
                    WHERE org_id = %s
                    """,
                    (org_id,),
                ).fetchall()
            }

            evidence_day = _is_first_business_day()
            plans: list[
                tuple[
                    str,
                    list[NudgeCandidate],
                    dict[str, int],
                    dict[str, int],
                    int,
                    str,
                    Evidence | None,
                ]
            ] = []
            for rep_id, items in sorted(by_rep.items()):
                selected, _eligible, shown, stock = _select_for_rep(items)
                if not selected:
                    continue
                suitable = suitable_raw.get(
                    rep_id, {t: 0 for t in _TYPE_ORDER}
                )
                name, email = reps.get(rep_id, (rep_id, None))
                evidence = _pick_evidence(conn, org_id, rep_id)
                week = _load_week_compare(conn, rep_id)
                msg = _build_message(
                    rep_name=name,
                    rep_email=email,
                    selected=selected,
                    stock=stock,
                    suitable=suitable,
                    evidence=evidence,
                    week=week,
                )
                plans.append(
                    (rep_id, selected, suitable, shown, stock, msg, evidence)
                )
                produced += len(selected)

            print(
                f"aday: {len(raw)} (dedup atlanan={skipped_dup}), "
                f"isimli={named}, temsilci={len(plans)}, "
                f"dürtü={produced} (org={org_id})"
            )
            if evidence_day:
                print("kanıt: haftanın ilk iş günü — mesaja eklenecek")
            else:
                print("kanıt: atlandı (haftanın ilk iş günü değil)")
            for (
                rep_id, selected, suitable, shown, stock, _msg, evidence
            ) in plans:
                name, _email = reps.get(rep_id, (rep_id, None))
                detail = ", ".join(
                    f"{t} uygun={suitable[t]}/gösterilen={shown[t]}"
                    for t in _TYPE_ORDER
                    if suitable[t] or shown[t]
                )
                ev_s = evidence.id if evidence else "-"
                print(
                    f"  {name}: toplam={len(selected)} ({detail}), "
                    f"stok={stock}, kanıt={ev_s}"
                )

            if dry_run:
                print("dry-run: gönderilmedi. Yazmak için --apply kullan.")
                if plans:
                    print("--- örnek mesaj ---")
                    print(plans[0][5])
            else:
                assert webhook_url and shadow_email
                for (
                    rep_id, selected, suitable, shown, stock, msg, evidence
                ) in plans:
                    name, email = reps.get(rep_id, (rep_id, None))
                    try:
                        _post_cliq(webhook_url, msg, shadow_email)
                    except (
                        urllib.error.URLError,
                        urllib.error.HTTPError,
                        TimeoutError,
                    ) as exc:
                        errors += 1
                        print(f"hata (cliq {name}): {exc}")
                        continue

                    for n in selected:
                        try:
                            payload: dict[str, Any] = {
                                "shadow": True,
                                "shadow_email": shadow_email,
                                "intended_rep_id": rep_id,
                                "intended_rep_name": name,
                                "intended_rep_email": email,
                                "nudge_type": n.nudge_type,
                                "phone": n.phone,
                                "contact_name": n.contact_name,
                                "stock": stock,
                                "suitable": suitable,
                                "shown": shown,
                            }
                            if evidence is not None:
                                payload["evidence_id"] = evidence.id
                            sent += _insert_nudge(
                                conn, org_id=org_id, n=n, payload=payload
                            )
                        except psycopg.Error as exc:
                            errors += 1
                            print(
                                f"hata (nudges {name}/{n.nudge_type}): {exc}"
                            )
    except psycopg.Error as exc:
        print(f"bağlantı/sorgu başarısız: {exc}")
        errors += 1
        print(f"üretilen={produced}, gönderilen={sent}, hata={errors}")
        return 1

    print(f"üretilen={produced}, gönderilen={sent}, hata={errors}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
