"""Kimlik çözümleme modülü.

thread_id Zoho ID'sine değil, bizim ürettiğimiz kalıcı bir kimliğe
bağlanır. Zoho lead -> contact dönüşümünde Zoho yeni ID üretse de aynı
telefon/e-posta üzerinden hat korunur. Birden fazla thread aynı kişiye
çıkarsa deterministik olarak birleştirilir: en eski thread kazanır.
LLM yok; tüm mantık bu dosyadaki koddur.

Hiçbir geçerli (ve bloklanmamış) tanımlayıcı yoksa None döner;
yeni thread açılmaz.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

import psycopg

from pusula.db import client

# Boşluk, tire, parantez ve nokta telefon girdisinden temizlenir.
_PHONE_JUNK_RE = re.compile(r"[ \-().]")


def _is_valid_turkish_national(digits: str) -> bool:
    """Ulusal numara 10 hane; ilk hane: 2-4 sabit hat, 5 mobil,
    8 kurumsal (850), 9 rezerve."""
    return len(digits) == 10 and digits.isdigit() and digits[0] in "234589"


def normalize_phone(raw: str) -> str | None:
    """Telefonu E.164 biçimine çevirir (Türkiye + uluslararası).

    Türkiye: "0532…", "532…", "+90…", "0090…", "905…" → "+905…".
    Geçersiz TR ulusal numara None döner.
    Yurt dışı: "+" veya "00" ile gelirse temizlenmiş E.164 korunur
    (ör. +49176…, +1469…). Ülke kodu olmadan ≥11 haneli rakam
    dizisi de "+" eklenerek korunur (Zoho bazen + yazmaz).
    """
    cleaned = _PHONE_JUNK_RE.sub("", raw.strip())
    if not cleaned:
        return None

    # Uluslararası 00 öneki + ile eşdeğerdir.
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit():
            return None
        if digits.startswith("90"):
            national = digits[2:]
            return "+90" + national if _is_valid_turkish_national(national) else None
        # Yurt dışı: temizlenmiş haline dokunma.
        return "+" + digits

    if not cleaned.isdigit():
        return None

    # Ülke kodu olmadan yazılmış Türkiye varyantları.
    if cleaned.startswith("90") and len(cleaned) == 12:
        national = cleaned[2:]
        return "+90" + national if _is_valid_turkish_national(national) else None
    if cleaned.startswith("0") and len(cleaned) == 11:
        national = cleaned[1:]
        return "+90" + national if _is_valid_turkish_national(national) else None
    if len(cleaned) == 10:
        return "+90" + cleaned if _is_valid_turkish_national(cleaned) else None

    # TR değil; yeterince uzun rakam → uluslararası E.164 adayı (+ ekle).
    if len(cleaned) >= 11:
        return "+" + cleaned
    return None


def _is_blocked(id_type: str, id_value: str) -> bool:
    """Tanımlayıcı çözümlemede yok sayılmalı mı (blocklist cache)."""
    return client.is_identifier_blocked(id_type, id_value)


def normalize_email(raw: str) -> str | None:
    """E-postayı küçük harfe çevirir ve kırpar.

    Gmail nokta/plus normalizasyonu yapılmaz. Geçersiz biçim None döner.
    """
    cleaned = raw.strip().lower()
    local, sep, domain = cleaned.partition("@")
    if not sep or not local or "." not in domain:
        return None
    return cleaned


def resolve_thread(
    phone: str | None = None,
    email: str | None = None,
    zoho_lead_id: str | None = None,
    zoho_contact_id: str | None = None,
    *,
    conn: psycopg.Connection[Any] | None = None,
) -> str | None:
    """Verilen kimlikleri kalıcı bir thread_id'ye çözer.

    - Geçerli tanımlayıcı yoksa (hepsi boş/geçersiz/bloklu) None döner;
      yeni thread açılmaz.
    - Eşleşme yoksa yeni thread açar.
    - Tek thread eşleşirse eksik kimlikleri ona ekler.
    - Birden fazla thread eşleşirse en eski created_at kazanır;
      kaybedenlerin satırları taşınır, thread_merges'e kayıt düşülür.
    conn verilirse dış transaction kullanılır; yoksa kendi transaction'ı.
    """
    if conn is not None:
        thread_id, _created_new = _resolve_thread_on_conn(
            conn,
            phone=phone,
            email=email,
            zoho_lead_id=zoho_lead_id,
            zoho_contact_id=zoho_contact_id,
        )
        return thread_id

    with client.transaction() as owned_conn:
        thread_id, _created_new = _resolve_thread_on_conn(
            owned_conn,
            phone=phone,
            email=email,
            zoho_lead_id=zoho_lead_id,
            zoho_contact_id=zoho_contact_id,
        )
        return thread_id


def resolve_thread_detailed(
    phone: str | None = None,
    email: str | None = None,
    zoho_lead_id: str | None = None,
    zoho_contact_id: str | None = None,
    *,
    conn: psycopg.Connection[Any],
) -> tuple[str | None, bool]:
    """resolve_thread + created_new bayrağı (ingest transaction için)."""
    return _resolve_thread_on_conn(
        conn,
        phone=phone,
        email=email,
        zoho_lead_id=zoho_lead_id,
        zoho_contact_id=zoho_contact_id,
    )


def _resolve_thread_on_conn(
    conn: psycopg.Connection[Any],
    *,
    phone: str | None,
    email: str | None,
    zoho_lead_id: str | None,
    zoho_contact_id: str | None,
) -> tuple[str | None, bool]:
    """Dönüş: (thread_id | None, created_new)."""
    # a) Kimlikleri normalize et; geçersiz olanlar sessizce düşer.
    pairs: list[tuple[str, str]] = []
    if phone is not None:
        normalized_phone = normalize_phone(phone)
        if normalized_phone is not None:
            pairs.append(("phone", normalized_phone))
    if email is not None:
        normalized_email = normalize_email(email)
        if normalized_email is not None:
            pairs.append(("email", normalized_email))
    if zoho_lead_id is not None and zoho_lead_id.strip():
        pairs.append(("zoho_lead", zoho_lead_id.strip()))
    if zoho_contact_id is not None and zoho_contact_id.strip():
        pairs.append(("zoho_contact", zoho_contact_id.strip()))

    # a) Bloklu tanımlayıcılar tamamen yok sayılır.
    active_pairs = [
        (id_type, id_value)
        for id_type, id_value in pairs
        if not _is_blocked(id_type, id_value)
    ]
    if not active_pairs:
        return None, False

    # b) Mevcut eşleşmeleri topla (hangi kimlik hangi thread'e çıkıyor).
    matches: dict[tuple[str, str], str] = {}
    for id_type, id_value in active_pairs:
        found = client.find_identity_thread_id(conn, id_type, id_value)
        if found is not None:
            matches[(id_type, id_value)] = found
    matched_thread_ids = sorted(set(matches.values()))

    created_new = False
    if not matched_thread_ids:
        # c) Hiç eşleşme yok: yeni thread aç.
        thread_id = uuid4().hex
        client.create_thread(conn, thread_id)
        created_new = True
    elif len(matched_thread_ids) == 1:
        # d) Tek thread: eksik kimlikler aşağıda eklenir.
        thread_id = matched_thread_ids[0]
    else:
        # e) Birden fazla thread: en eski created_at kazanır.
        winner = client.pick_oldest_thread(conn, matched_thread_ids)
        for loser in matched_thread_ids:
            if loser == winner:
                continue
            reason = ", ".join(
                f"{id_type}={id_value}"
                for (id_type, id_value), tid in matches.items()
                if tid == loser
            )
            client.reassign_thread_rows(conn, winner, loser)
            client.record_thread_merge(conn, winner, loser, reason)
            client.delete_thread(conn, loser)
        thread_id = winner

    # Tüm aktif kimlikleri (yeni + mevcut) thread'e bağla.
    for id_type, id_value in active_pairs:
        client.upsert_identity(conn, thread_id, id_type, id_value)

    return thread_id, created_new
