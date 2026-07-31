"""Zoho CRM modül alanlarını metadata üzerinden listeler.

v7 get_records fields parametresi zorunlu olduğu için önce
settings/fields ile alan listesini alır; isteğe bağlı örnek
kayıtlarda değerleri olduğu gibi basmaz (gizlilik).

Kullanım:
    python scripts/inspect_zoho_module.py --module Calls
    python scripts/inspect_zoho_module.py --module Calls --with-sample --limit 3

.env otomatik yüklenir.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from itertools import islice
from pathlib import Path
from typing import Any

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import get_field_metadata, get_records

# Zoho get_records tek istekte en fazla 50 alan kabul eder.
_MAX_FIELDS_PER_REQUEST = 50

# Picklist / kısa enum alanlarında değeri basmak güvenli (kişisel veri değil).
_VALUE_SAFE_DATA_TYPES = frozenset({"picklist", "boolean"})


def _chunks(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _value_type_name(value: Any) -> str:
    if value is None:
        return "None"
    return type(value).__name__


def _format_sample_field(
    api_name: str, value: Any, data_type: str | None
) -> str:
    """Gizlilik: tip + dolu/boş; enum benzeri alanlarda değer de basılır."""
    filled = "boş" if _is_empty(value) else "dolu"
    type_name = _value_type_name(value)
    line = f"  {api_name}: {type_name}, {filled}"
    if (
        data_type in _VALUE_SAFE_DATA_TYPES
        and not _is_empty(value)
        and isinstance(value, (str, bool, int, float))
    ):
        line += f", değer={value!r}"
    return line


def _print_metadata(module: str, fields: list[dict[str, Any]]) -> None:
    print(f"{module}: {len(fields)} alan (metadata)\n")
    print(f"{'api_name':<40} {'field_label':<30} {'data_type':<16} custom")
    print(f"{'-' * 40} {'-' * 30} {'-' * 16} ------")
    for field in sorted(fields, key=lambda f: f["api_name"]):
        custom = "yes" if field["custom_field"] else "no"
        label = field.get("field_label") or ""
        data_type = field.get("data_type") or ""
        print(
            f"{field['api_name']:<40} {label:<30} {data_type:<16} {custom}"
        )


def _fetch_sample_records(
    module: str, api_names: Sequence[str], limit: int
) -> list[dict[str, Any]]:
    """fields zorunlu ve ≤50 olduğu için parçalı çekip id ile birleştirir."""
    if not api_names:
        return []

    # id birleştirme anahtarı; her parçada olsun.
    names = list(api_names)
    if "id" not in names:
        names = ["id", *names]

    chunks = _chunks(names, _MAX_FIELDS_PER_REQUEST)
    selected: list[dict[str, Any]] = []
    for record in islice(get_records(module, chunks[0], per_page=limit), limit):
        selected.append(dict(record))

    if len(chunks) == 1 or not selected:
        return selected

    wanted = {str(r["id"]) for r in selected if r.get("id") is not None}
    by_id = {str(r["id"]): r for r in selected if r.get("id") is not None}

    for chunk in chunks[1:]:
        remaining = set(wanted)
        # id her parçada şart; chunk'ta yoksa ekle.
        chunk_fields = list(chunk)
        if "id" not in chunk_fields:
            chunk_fields = ["id", *chunk_fields][:_MAX_FIELDS_PER_REQUEST]
        for record in get_records(module, chunk_fields):
            rid = record.get("id")
            if rid is None:
                continue
            key = str(rid)
            if key not in remaining:
                continue
            by_id[key].update(record)
            remaining.discard(key)
            if not remaining:
                break

    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zoho CRM modül alan metadata'sını (ve isteğe bağlı örnekleri) basar."
    )
    parser.add_argument("--module", required=True, help="CRM modül API adı (örn. Calls)")
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="--with-sample ile çekilecek kayıt sayısı (varsayılan: 3)",
    )
    parser.add_argument(
        "--with-sample",
        action="store_true",
        help="metadata sonrası örnek kayıt çek (değerleri gizleyerek)",
    )
    args = parser.parse_args()

    if args.limit < 1:
        print("--limit en az 1 olmalı")
        return 1

    load_dotenv()

    try:
        fields = get_field_metadata(args.module)
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"{args.module}: metadata HATA — {exc}")
        return 1

    if not fields:
        print(f"{args.module}: alan metadata'sı boş")
        return 0

    _print_metadata(args.module, fields)

    if not args.with_sample:
        return 0

    api_names = [f["api_name"] for f in fields]
    data_types = {f["api_name"]: f.get("data_type") for f in fields}

    print()
    try:
        records = _fetch_sample_records(args.module, api_names, args.limit)
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"{args.module}: örnek kayıt HATA — {exc}")
        return 1

    if not records:
        print(f"{args.module}: örnek kayıt yok")
        return 0

    print(f"{args.module}: {len(records)} örnek kayıt (değerler gizli)\n")
    for index, record in enumerate(records, start=1):
        print(f"--- kayıt {index} ---")
        for key in sorted(record.keys()):
            print(_format_sample_field(key, record[key], data_types.get(key)))
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
