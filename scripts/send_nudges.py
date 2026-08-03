"""Günlük dürtü üretir ve Cliq'e gölge modda gönderir.

Üç sinyal (temsilci başına toplam en fazla 3):
  1. pencere_aciliyor — active lead, outbound < 3
  2. kayip_randevu    — Randevu Alındı var, Sunum Yok, sonrası temas yok
  3. gecikmis_taahhut — broken commitment, due_at son 14 gün

Kontenjan: önce her tipten en fazla 1; kalan slot öncelik
sırasıyla (pencere → kayıp randevu → taahhüt) doldurulur.

Haftanın ilk iş gününde (Pazartesi, Europe/Istanbul) mesajın
sonuna havuzdan bir kanıt eklenir. Aynı kanıt aynı temsilciye
8 hafta içinde tekrar gitmez; evidence_id nudges.payload'a yazılır.

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
from datetime import datetime
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
        ) AS phone
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
        r.randevu_at
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

_TAAHHUT_SQL = """
    SELECT
        coalesce(e.rep_id, t.owner_rep_id) AS rep_id,
        c.thread_id,
        (
            SELECT l.lead_id FROM leads l
            WHERE l.org_id = c.org_id AND l.thread_id = c.thread_id
            ORDER BY l.assigned_at DESC NULLS LAST
            LIMIT 1
        ) AS lead_id,
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
        c.text AS commitment_text
    FROM commitments c
    LEFT JOIN events e ON e.id = c.source_event_id
    LEFT JOIN threads t
      ON t.org_id = c.org_id AND t.thread_id = c.thread_id
    WHERE c.org_id = %s
      AND c.status = 'broken'
      AND c.due_at IS NOT NULL
      AND c.due_at >= now() - interval '14 days'
      AND c.due_at < now()
      AND c.thread_id IS NOT NULL
      AND coalesce(e.rep_id, t.owner_rep_id) IS NOT NULL
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


@dataclass
class NudgeCandidate:
    rep_id: str
    thread_id: str
    lead_id: str | None
    commitment_id: int | None
    nudge_type: str
    sort_key: float
    phone: str | None
    outbound_calls: int | None = None
    randevu_at: datetime | None = None
    due_at: datetime | None = None
    commitment_text: str | None = None


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


def _line_for(n: NudgeCandidate) -> str:
    phone = n.phone or "telefon yok"
    if n.nudge_type == "pencere_aciliyor":
        calls = n.outbound_calls if n.outbound_calls is not None else 0
        return (
            f"{phone} — pazarsız {_fmt_hours(n.sort_key)}, "
            f"{calls} arama"
        )
    if n.nudge_type == "kayip_randevu":
        return f"{phone} — randevu {_fmt_dt(n.randevu_at)}, sonrası temas yok"
    due = _fmt_dt(n.due_at)
    note = (n.commitment_text or "").strip()
    if note:
        note = note.replace("\n", " ")
        if len(note) > 60:
            note = note[:57] + "..."
        return f"{phone} — vade {due}: {note}"
    return f"{phone} — vade {due}"


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


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else full_name


def _build_message(
    *,
    rep_name: str,
    rep_email: str | None,
    selected: list[NudgeCandidate],
    stock: int,
    evidence: Evidence | None = None,
) -> str:
    who = rep_name
    if rep_email:
        who = f"{rep_name} <{rep_email}>"
    parts: list[str] = [
        f"[gölge] {who} için",
        "",
        f"Günaydın {_first_name(rep_name)}",
        "",
    ]
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in selected:
        by_type[n.nudge_type].append(n)
    for nudge_type in _TYPE_ORDER:
        items = by_type.get(nudge_type) or []
        if not items:
            continue
        parts.append(_TYPE_TITLE[nudge_type])
        for item in items:
            parts.append(_line_for(item))
        parts.append("")
    parts.append(f"Bekleyen stok: {stock}")
    if evidence is not None:
        parts.append("")
        parts.append(evidence.metin)
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
            sort_key, outbound_calls, phone,
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
                outbound_calls=int(outbound_calls or 0),
            )
        )

    for row in conn.execute(_KAYIP_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, phone, randevu_at,
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
                randevu_at=randevu_at,
            )
        )

    for row in conn.execute(_TAAHHUT_SQL, (org_id,)).fetchall():
        (
            rep_id, thread_id, lead_id, commitment_id, nudge_type,
            sort_key, outbound_calls, phone, due_at, commitment_text,
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
                due_at=due_at,
                commitment_text=str(commitment_text) if commitment_text else None,
            )
        )

    return out


def _select_for_rep(items: list[NudgeCandidate]) -> tuple[list[NudgeCandidate], int]:
    """Her tipten 1, kalan kontenjan öncelik sırasıyla; toplam max 3."""
    by_type: dict[str, list[NudgeCandidate]] = defaultdict(list)
    for n in items:
        by_type[n.nudge_type].append(n)
    for nudge_type in _TYPE_ORDER:
        by_type[nudge_type].sort(key=lambda x: x.sort_key, reverse=True)

    selected: list[NudgeCandidate] = []
    selected_ids: set[tuple[str, str, str]] = set()

    def _key(n: NudgeCandidate) -> tuple[str, str, str]:
        return (n.nudge_type, n.thread_id, str(n.commitment_id or n.lead_id or ""))

    # Tur 1: her tipten en fazla 1.
    for nudge_type in _TYPE_ORDER:
        if len(selected) >= _MAX_PER_REP:
            break
        pool = by_type.get(nudge_type) or []
        if not pool:
            continue
        n = pool[0]
        selected.append(n)
        selected_ids.add(_key(n))

    # Tur 2: kalan slotları öncelik sırasıyla doldur.
    if len(selected) < _MAX_PER_REP:
        for nudge_type in _TYPE_ORDER:
            if len(selected) >= _MAX_PER_REP:
                break
            for n in by_type.get(nudge_type) or []:
                if len(selected) >= _MAX_PER_REP:
                    break
                k = _key(n)
                if k in selected_ids:
                    continue
                selected.append(n)
                selected_ids.add(k)

    stock = max(0, len(items) - len(selected))
    return selected, stock


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
                tuple[str, list[NudgeCandidate], int, str, Evidence | None]
            ] = []
            for rep_id, items in sorted(by_rep.items()):
                selected, stock = _select_for_rep(items)
                if not selected:
                    continue
                name, email = reps.get(rep_id, (rep_id, None))
                evidence = _pick_evidence(conn, org_id, rep_id)
                msg = _build_message(
                    rep_name=name,
                    rep_email=email,
                    selected=selected,
                    stock=stock,
                    evidence=evidence,
                )
                plans.append((rep_id, selected, stock, msg, evidence))
                produced += len(selected)

            print(
                f"aday: {len(raw)} (dedup atlanan={skipped_dup}), "
                f"temsilci={len(plans)}, dürtü={produced} (org={org_id})"
            )
            if evidence_day:
                print("kanıt: haftanın ilk iş günü — mesaja eklenecek")
            else:
                print("kanıt: atlandı (haftanın ilk iş günü değil)")
            for rep_id, selected, stock, _msg, evidence in plans:
                name, _email = reps.get(rep_id, (rep_id, None))
                by_t = defaultdict(int)
                for n in selected:
                    by_t[n.nudge_type] += 1
                detail = ", ".join(
                    f"{t}={by_t[t]}" for t in _TYPE_ORDER if by_t[t]
                )
                ev_s = evidence.id if evidence else "-"
                print(
                    f"  {name}: {len(selected)} dürtü ({detail}), "
                    f"stok={stock}, kanıt={ev_s}"
                )

            if dry_run:
                print("dry-run: gönderilmedi. Yazmak için --apply kullan.")
            else:
                assert webhook_url and shadow_email
                for rep_id, selected, stock, msg, evidence in plans:
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
                                "stock": stock,
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
