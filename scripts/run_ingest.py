"""Kayıtlı ingester'ları çalıştırır ve özet tablosu basar.

Kullanım:
    python scripts/run_ingest.py --source verimor_calls
    python scripts/run_ingest.py --source all --since 2026-07-01T00:00:00
    python scripts/run_ingest.py --source zoho_crm_calls --dry-run --limit 50

--source all kayıtlı tüm ingester'ları sırayla çalıştırır. --since
ISO 8601 tarih alır; saat dilimi verilmemişse Europe/Istanbul sayılır.
--limit verilirse o kadar kayıt çekildikten sonra fetch durur.
--debug-query oluşturulmuş COQL'i basar (kişisel veri yok).
--dry-run DB'ye yazmaz, sayıları ve örnek Event'leri gösterir.
.env otomatik yüklenir; DATABASE_URL gerekir.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.ingest import IngestResult, Ingester, all_ingesters, get
from pusula.ingest.base import RawRecord, to_istanbul

logger = logging.getLogger(__name__)

_RESULT_COLUMNS = [
    "source", "channel", "fetched", "inserted",
    "duplicated", "skipped", "failed", "watermark_after",
]
_PROGRESS_EVERY = 200


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
    """Hata, atlama sebepleri ve dry_run örneklerini basar."""
    if result.errors:
        print(f"\n{result.source_name} hataları (ilk {len(result.errors)}):")
        for message in result.errors:
            print(f"  {message}")

    if result.skip_reasons:
        print(f"\nATLAMA SEBEPLERİ ({result.source_name}):")
        for reason, count in sorted(
            result.skip_reasons.items(), key=lambda item: (-item[1], item[0])
        ):
            print(f"  {reason}: {count}")

    if result.sample_events:
        print(f"\n{result.source_name} örnek event'ler (dry-run):")
        for event in result.sample_events:
            occurred = (
                event.occurred_at.isoformat(timespec="seconds")
                if event.occurred_at is not None
                else "-"
            )
            print(f"  {occurred}  {event.channel}  {event.source_ref}  rep={event.rep_id}")
    elif result.sample_skipped:
        print(f"\n{result.source_name} atlanan örnekler (dry-run):")
        for index, sample in enumerate(result.sample_skipped, start=1):
            print(f"  --- atlanan {index} ---")
            for key, value in sample.items():
                print(f"    {key}: {value}")


def _apply_fetch_options(
    ingester: Ingester, limit: int | None, debug_query: bool
) -> None:
    """fetch limit, ilerleme logu ve debug_query; base.py'ye dokunulmaz."""
    if hasattr(ingester, "fetch_limit"):
        setattr(ingester, "fetch_limit", limit)
    if hasattr(ingester, "debug_query"):
        setattr(ingester, "debug_query", debug_query)

    original_fetch = ingester.fetch

    def fetch_with_progress(since: datetime | None) -> Iterator[RawRecord]:
        count = 0
        for raw in original_fetch(since):
            count += 1
            if count % _PROGRESS_EVERY == 0:
                logger.info("%s kayıt işlendi", count)
            yield raw
            if limit is not None and count >= limit:
                break

    ingester.fetch = fetch_with_progress  # type: ignore[method-assign]


def main() -> int:
    parser = argparse.ArgumentParser(description="Kayıtlı ingester'ları çalıştırır.")
    parser.add_argument("--source", required=True, help="ingester adı veya 'all'")
    parser.add_argument("--since", help="ISO 8601 başlangıç zamanı (watermark yerine)")
    parser.add_argument(
        "--limit",
        type=int,
        help="en fazla kaç kayıt çekileceği (test için)",
    )
    parser.add_argument("--dry-run", action="store_true", help="DB'ye yazma, sayıları göster")
    parser.add_argument(
        "--debug-query",
        action="store_true",
        help="oluşturulan COQL sorgusunu ekrana bas (kişisel veri yok)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.debug_query:
        logging.getLogger("pusula.ingest.crm_calls").setLevel(logging.DEBUG)
    load_dotenv()

    if args.limit is not None and args.limit < 1:
        print("--limit en az 1 olmalı")
        return 1

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
    identity_notes: list[str] = []
    unknown_picklists: list[tuple[str, str, dict[str, int]]] = []
    had_error = False
    for ingester_cls in ingester_classes:
        ingester = ingester_cls()
        _apply_fetch_options(ingester, args.limit, args.debug_query)
        try:
            results.append(ingester.run(since=since, dry_run=args.dry_run))
            stats = getattr(ingester, "lead_identity_stats", None)
            if isinstance(stats, dict):
                identity_notes.append(
                    f"kimlik zenginlestirme: islenen={stats.get('processed', 0)} "
                    f"(gorulen={stats.get('leads_seen', stats.get('processed', 0))}), "
                    f"+{stats.get('phones_added', 0)} telefon, "
                    f"+{stats.get('emails_added', 0)} e-posta, "
                    f"leads yazilan={stats.get('leads_written', 0)}, "
                    f"hata={stats.get('errors', 0)}"
                )
            unknown_outcomes = getattr(ingester, "unknown_outcomes", None)
            if isinstance(unknown_outcomes, dict) and unknown_outcomes:
                unknown_picklists.append(
                    (ingester_cls.source_name, "outcome", dict(unknown_outcomes))
                )
            unknown_statuses = getattr(ingester, "unknown_statuses", None)
            if isinstance(unknown_statuses, dict) and unknown_statuses:
                unknown_picklists.append(
                    (ingester_cls.source_name, "call_status", dict(unknown_statuses))
                )
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
        for note in identity_notes:
            print(f"\n{note}")
        if unknown_picklists:
            print("\nBİLİNMEYEN PICKLIST DEĞERLERİ")
            for source_name, kind, counts in unknown_picklists:
                print(f"  ({source_name} / {kind})")
                for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
                    print(f"    {value!r}: {count}")

    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
