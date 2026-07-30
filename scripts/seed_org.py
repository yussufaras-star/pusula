"""Bir org'u kurar: orgs kaydını açar, seed SQL dosyasını yükler.

Kullanım:
    python scripts/seed_org.py --org rexven --file pusula/db/seed_rexven.sql

Idempotenttir, iki kez çalıştırılabilir: orgs kaydı varsa dokunulmaz,
seed dosyasındaki upsert'ler tanımları tazeler. .env otomatik yüklenir;
DATABASE_URL gerekir.
"""

import argparse
import os
import sys
from pathlib import Path

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg
from dotenv import load_dotenv


def main() -> int:
    parser = argparse.ArgumentParser(description="Bir org'un seed verisini yükler.")
    parser.add_argument("--org", required=True, help="org_id (ör. rexven)")
    parser.add_argument("--file", required=True, help="seed SQL dosyası")
    parser.add_argument("--name", help="org görünen adı (varsayılan: org_id)")
    args = parser.parse_args()

    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL ortam değişkeni tanımlı değil")
        return 1

    seed_path = Path(args.file)
    if not seed_path.is_file():
        print(f"seed dosyası bulunamadı: {seed_path}")
        return 1
    seed_sql = seed_path.read_text(encoding="utf-8")

    # Tek transaction: org kaydı + seed birlikte girer ya da hiç girmez.
    try:
        with psycopg.connect(database_url) as conn:
            conn.execute(
                "INSERT INTO orgs (org_id, name) VALUES (%s, %s)"
                " ON CONFLICT (org_id) DO NOTHING",
                (args.org, args.name or args.org),
            )
            conn.execute(seed_sql)
            bug_code_count = conn.execute(
                "SELECT count(*) FROM bug_codes WHERE org_id = %s", (args.org,)
            ).fetchone()[0]
            segment_count = conn.execute(
                "SELECT count(*) FROM segments WHERE org_id = %s", (args.org,)
            ).fetchone()[0]
    except psycopg.Error as exc:
        print(f"seed yüklenemedi: {exc}")
        return 1

    print(f"org '{args.org}': OK")
    print(f"  bug_codes: {bug_code_count}")
    print(f"  segments: {segment_count}")
    if bug_code_count == 0 and segment_count == 0:
        print("  uyarı: bu org için hiç kayıt yok; seed dosyası başka bir org'a ait olabilir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
