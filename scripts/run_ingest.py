"""Kayıtlı ingester'ları çalıştırır ve özet tablosu basar.

Kullanım:
    python scripts/run_ingest.py --source verimor_calls
    python scripts/run_ingest.py --source all --since 2026-07-01T00:00:00
    python scripts/run_ingest.py --source all --dry-run

--source all kayıtlı tüm ingester'ları sırayla çalıştırır. --since
ISO 8601 tarih alır; saat dilimi verilmemişse Europe/Istanbul sayılır.
--dry-run DB'ye yazmaz, sayıları ve örnek Event'leri gösterir.
.env otomatik yüklenir; DATABASE_URL gerekir.
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest import IngestResult, all_ingesters, get
from pusula.ingest.base import to_istanbul

_RESULT_COLUMNS = [
    "source", "channel", "fetched", "inserted",
    "duplicated", "skipped", "failed", "watermark_after",
]


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Basit hizalanmış metin tablosu basar."""
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def result_row(result: IngestResult) -> list[str]:
    watermark = (
        result.watermark_after.isoformat(timespec="seconds")
        if result.watermark_after is not None
        else "-"
    )
    return [
        result.source_name,
        result.channel,
        str(result.fetched),
        str(result.inserted),
        str(result.duplicated),
        str(result.skipped),
        str(result.failed),
        watermark,
    ]


def print_details(result: IngestResult) -> None:
    """Hata mesajlarını ve dry_run örnek Event'lerini basar."""
    if result.errors:
        print(f"\n{result.source_name} hataları (ilk {len(result.errors)}):")
        for message in result.errors:
            print(f"  {message}")
    if result.sample_events:
        print(f"\n{result.source_name} örnek event'ler (dry-run):")
        for event in result.sample_events:
            occurred = (
                event.occurred_at.isoformat(timespec="seconds")
                if event.occurred_at is not None
                else "-"
            )
            print(f"  {occurred}  {event.channel}  {event.source_ref}  rep={event.rep_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Kayıtlı ingester'ları çalıştırır.")
    parser.add_argument("--source", required=True, help="ingester adı veya 'all'")
    parser.add_argument("--since", help="ISO 8601 başlangıç zamanı (watermark yerine)")
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sayıları göster")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()

    since: datetime | None = None
    if args.since:
        try:
            since = to_istanbul(datetime.fromisoformat(args.since))
        except ValueError:
            print(f"--since çözümlenemedi: {args.since!r} (ISO 8601 bekleniyor)")
            return 1

    if args.source == "all":
        ingester_classes = all_ingesters()
        if not ingester_classes:
            print("kayıtlı ingester yok; somut ingester'lar eklendikçe burada görünür")
            return 0
    else:
        try:
            ingester_classes = [get(args.source)]
        except KeyError as exc:
            print(exc.args[0])
            return 1

    results: list[IngestResult] = []
    had_error = False
    for ingester_cls in ingester_classes:
        ingester = ingester_cls()
        try:
            results.append(ingester.run(since=since, dry_run=args.dry_run))
        except Exception as exc:
            # Bir kaynağın çökmesi diğerlerini engellemesin.
            had_error = True
            print(f"{ingester_cls.source_name}: run yarıda kesildi — {exc}")

    if results:
        if args.dry_run:
            print("dry-run: DB'ye hiçbir şey yazılmadı\n")
        print_table(_RESULT_COLUMNS, [result_row(r) for r in results])
        for result in results:
            print_details(result)

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
