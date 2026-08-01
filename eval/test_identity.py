"""normalize_phone ve resolve_thread için temel senaryolar.

DB testleri DATABASE_URL ister; tanımlı değilse atlanır. Şema her test
oturumunda idempotent olarak uygulanır, tablolar test başına temizlenir.
Çalıştırma: DATABASE_URL=... pytest eval/test_identity.py
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from pusula.db.identity import normalize_phone, resolve_thread

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "pusula" / "db" / "schema.sql"


# --- normalize_phone ---


@pytest.mark.parametrize(
    "raw",
    [
        "0532 123 45 67",
        "532 123 45 67",
        "+90 532 123 45 67",
        "00905321234567",
        "905321234567",
    ],
)
def test_normalize_phone_turkish_variants(raw: str) -> None:
    assert normalize_phone(raw) == "+905321234567"


@pytest.mark.parametrize("raw", ["", "  ", "123", "0532 123 45", "abc", "05321234567890"])
def test_normalize_phone_invalid(raw: str) -> None:
    assert normalize_phone(raw) is None


def test_normalize_phone_foreign() -> None:
    # Yurt dışı numarada sadece boşluk/tire temizlenir, dokunulmaz.
    assert normalize_phone("+44 20-7946 0958") == "+442079460958"


@pytest.mark.parametrize("raw", ["8505323576", "0850 532 35 76", "+90 850 532 35 76"])
def test_normalize_phone_corporate_850(raw: str) -> None:
    # 850'li kurumsal numaralar geçerli sayılır.
    assert normalize_phone(raw) == "+908505323576"


# --- resolve_thread (DB gerekli) ---


@pytest.fixture()
def db() -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL tanımlı değil")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(SCHEMA_PATH.read_text())
        conn.execute(
            "TRUNCATE identities, thread_merges, blocked_identifiers,"
            " blocked_domains, commitments, events, threads CASCADE"
        )
        yield conn


def test_resolve_creates_new_thread(db: psycopg.Connection) -> None:
    thread_id = resolve_thread(phone="0532 123 45 67")

    assert db.execute(
        "SELECT 1 FROM threads WHERE thread_id = %s", (thread_id,)
    ).fetchone() is not None
    row = db.execute(
        "SELECT thread_id FROM identities WHERE id_type = 'phone' AND id_value = %s",
        ("+905321234567",),
    ).fetchone()
    assert row == (thread_id,)


def test_resolve_matches_existing_thread(db: psycopg.Connection) -> None:
    thread_id = resolve_thread(phone="0532 123 45 67")

    # Aynı telefon farklı biçimde gelse de aynı thread'e çözülür,
    # yeni gelen e-posta da o thread'e bağlanır.
    same = resolve_thread(phone="+90 532 123 45 67", email="Ali@Example.com")
    assert same == thread_id
    assert resolve_thread(email="ali@example.com") == thread_id

    count = db.execute("SELECT count(*) FROM threads").fetchone()
    assert count == (1,)


def test_resolve_merges_threads(db: psycopg.Connection) -> None:
    # İki ayrı hat: biri telefonla, biri Zoho lead ile açılmış.
    old_thread = resolve_thread(phone="0532 123 45 67")
    new_thread = resolve_thread(zoho_lead_id="L-1")
    assert old_thread != new_thread

    # Kaybedecek hatta bir event bağla; merge'de taşınmalı.
    db.execute(
        "INSERT INTO events (thread_id, channel, source_ref) VALUES (%s, 'call', 'm-1')",
        (new_thread,),
    )

    # Dönüşüm anı: telefon + lead + contact aynı çağrıda. En eski kazanır.
    winner = resolve_thread(
        phone="532 123 45 67", zoho_lead_id="L-1", zoho_contact_id="C-1"
    )
    assert winner == old_thread

    # Kaybeden threads satırı silinmiş.
    assert db.execute(
        "SELECT 1 FROM threads WHERE thread_id = %s", (new_thread,)
    ).fetchone() is None

    # Tüm kimlikler kazanan thread'de.
    rows = db.execute(
        "SELECT id_type FROM identities WHERE thread_id = %s ORDER BY id_type",
        (winner,),
    ).fetchall()
    assert [r[0] for r in rows] == ["phone", "zoho_contact", "zoho_lead"]

    # Event kazanan thread'e taşınmış.
    assert db.execute(
        "SELECT thread_id FROM events WHERE source_ref = 'm-1'"
    ).fetchone() == (winner,)

    # Denetim kaydı düşülmüş ve tetikleyen eşleşmeyi anlatıyor.
    merge = db.execute(
        "SELECT winner_thread_id, loser_thread_id, reason FROM thread_merges"
    ).fetchone()
    assert merge is not None
    assert merge[0] == winner
    assert merge[1] == new_thread
    assert "zoho_lead=L-1" in merge[2]


def test_resolve_ignores_blocked_identifier(db: psycopg.Connection) -> None:
    thread_id = resolve_thread(phone="0532 123 45 67")

    db.execute(
        "INSERT INTO blocked_identifiers (id_type, id_value, note)"
        " VALUES ('phone', '+905321234567', 'santral')"
    )

    # Bloklu telefon eşleşmede kullanılmaz: e-posta yeni bir hat açar.
    other = resolve_thread(phone="0532 123 45 67", email="spam@example.com")
    assert other != thread_id

    # Bloklu kimlik identities'e de yazılmaz; sadece ilk hattın kaydı durur.
    count = db.execute(
        "SELECT count(*) FROM identities WHERE id_type = 'phone'"
    ).fetchone()
    assert count == (1,)


def test_resolve_ignores_blocked_domain(db: psycopg.Connection) -> None:
    db.execute(
        "INSERT INTO blocked_domains (domain, note) VALUES ('rexven.com', 'şirket içi')"
    )

    # Bloklu domaindeki e-posta yok sayılır: identities'e yazılmaz.
    first = resolve_thread(email="Ali@Rexven.com", phone="0532 123 45 67")
    count = db.execute(
        "SELECT count(*) FROM identities WHERE id_type = 'email'"
    ).fetchone()
    assert count == (0,)

    # Aynı bloklu e-posta tek başına gelirse eşleşme kuramaz, thread açılmaz.
    second = resolve_thread(email="ali@rexven.com")
    assert second is None


def test_resolve_processes_non_blocked_domain_email(db: psycopg.Connection) -> None:
    db.execute("INSERT INTO blocked_domains (domain) VALUES ('rexven.com')")

    # Domaini listede olmayan e-posta normal işlenir.
    thread_id = resolve_thread(email="Ali@Example.com")
    row = db.execute(
        "SELECT thread_id FROM identities"
        " WHERE id_type = 'email' AND id_value = 'ali@example.com'"
    ).fetchone()
    assert row == (thread_id,)
    assert resolve_thread(email="ali@example.com") == thread_id


def test_resolve_is_org_scoped(db: psycopg.Connection) -> None:
    # Başka org'un blok kayıtları rexven'i etkilemez; ORG_ID tanımlı
    # olmadığında tüm kayıtlar varsayılan org'a (rexven) yazılır.
    db.execute(
        "INSERT INTO blocked_identifiers (org_id, id_type, id_value)"
        " VALUES ('other_org', 'phone', '+905321234567')"
    )
    db.execute(
        "INSERT INTO blocked_domains (org_id, domain) VALUES ('other_org', 'example.com')"
    )

    thread_id = resolve_thread(phone="0532 123 45 67", email="ali@example.com")
    rows = db.execute(
        "SELECT org_id, id_type FROM identities WHERE thread_id = %s ORDER BY id_type",
        (thread_id,),
    ).fetchall()
    assert rows == [("rexven", "email"), ("rexven", "phone")]
    assert db.execute(
        "SELECT org_id FROM threads WHERE thread_id = %s", (thread_id,)
    ).fetchone() == ("rexven",)
