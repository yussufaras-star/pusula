"""Kimlik çözümleme modülü.

thread_id Zoho ID'sine değil, bizim ürettiğimiz kalıcı bir kimliğe
bağlanır. Zoho lead -> contact dönüşümünde Zoho yeni ID üretse de aynı
telefon/e-posta üzerinden hat korunur. Birden fazla thread aynı kişiye
çıkarsa deterministik olarak birleştirilir: en eski thread kazanır.
LLM yok; tüm mantık bu dosyadaki koddur.
"""

import re
from uuid import uuid4

from pusula.db import client

# Boşluk, tire, parantez ve nokta telefon girdisinden temizlenir.
_PHONE_JUNK_RE = re.compile(r"[ \-().]")


def _is_valid_turkish_national(digits: str) -> bool:
    # Ulusal numara 10 hane; alan kodu 2-4 (sabit hat) veya 5 (mobil) ile başlar.
    return len(digits) == 10 and digits.isdigit() and digits[0] in "2345"


def normalize_phone(raw: str) -> str | None:
    """Telefonu Türkiye odaklı E.164 biçimine çevirir.

    "0532 123 45 67", "532 123 45 67", "+90 532 123 45 67",
    "00905321234567" ve "905321234567" girdilerinin hepsi
    "+905321234567" döner. Geçersiz/eksik numara None döner.
    Yurt dışı numarası + ile başlıyorsa sadece boşluk/tire temizlenir.
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
    elif cleaned.startswith("0") and len(cleaned) == 11:
        national = cleaned[1:]
    elif len(cleaned) == 10:
        national = cleaned
    else:
        return None
    return "+90" + national if _is_valid_turkish_national(national) else None


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
) -> str:
    """Verilen kimlikleri kalıcı bir thread_id'ye çözer.

    - Eşleşme yoksa yeni thread açar.
    - Tek thread eşleşirse eksik kimlikleri ona ekler.
    - Birden fazla thread eşleşirse en eski created_at kazanır;
      kaybedenlerin satırları taşınır, thread_merges'e kayıt düşülür.
    Tüm işlem tek transaction içinde yürür. zoho_lead_id ve
    zoho_contact_id birlikte gelirse (dönüşüm anı) ikisi de aynı
    thread'e bağlanır.
    """
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

    with client.transaction() as conn:
        # a) Bloklu tanımlayıcılar tamamen yok sayılır.
        active_pairs = [
            (id_type, id_value)
            for id_type, id_value in pairs
            if not client.is_identifier_blocked(conn, id_type, id_value)
        ]

        # b) Mevcut eşleşmeleri topla (hangi kimlik hangi thread'e çıkıyor).
        matches: dict[tuple[str, str], str] = {}
        for id_type, id_value in active_pairs:
            found = client.find_identity_thread_id(conn, id_type, id_value)
            if found is not None:
                matches[(id_type, id_value)] = found
        matched_thread_ids = sorted(set(matches.values()))

        if not matched_thread_ids:
            # c) Hiç eşleşme yok: yeni thread aç.
            thread_id = uuid4().hex
            client.create_thread(conn, thread_id)
        elif len(matched_thread_ids) == 1:
            # d) Tek thread: eksik kimlikler aşağıda eklenir.
            thread_id = matched_thread_ids[0]
        else:
            # e) Birden fazla thread: en eski created_at kazanır.
            winner = client.pick_oldest_thread(conn, matched_thread_ids)
            for loser in matched_thread_ids:
                if loser == winner:
                    continue
                # Merge'ü tetikleyen eşleşme: kaybeden hatta çıkan kimlikler.
                reason = ", ".join(
                    f"{id_type}={id_value}"
                    for (id_type, id_value), tid in matches.items()
                    if tid == loser
                )
                client.reassign_thread_rows(conn, winner, loser)
                client.record_thread_merge(conn, winner, loser, reason)
                client.delete_thread(conn, loser)
            thread_id = winner

        # Tüm aktif kimlikleri (yeni + mevcut) thread'e bağla,
        # mevcutların last_seen_at'i güncellensin.
        for id_type, id_value in active_pairs:
            client.upsert_identity(conn, thread_id, id_type, id_value)

        return thread_id
