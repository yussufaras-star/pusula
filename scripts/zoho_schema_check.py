"""Zoho CRM şema drift kontrolü.

Modül alan metadata'sını (picklist değerleri dahil) çeker,
zoho_schema_snapshot ile karşılaştırır. Cron'da çıkış kodu 1 = fark var.

Kullanım:
    python scripts/zoho_schema_check.py --module Calls,Leads
    python scripts/zoho_schema_check.py --module Calls,Leads --save

.env otomatik yüklenir; DATABASE_URL ve Zoho credential gerekir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Json

from pusula.config import get_org_id
from pusula.db import client
from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request

_PICKLIST_TYPES = frozenset({"picklist", "multiselectpicklist"})


def _fetch_live_fields(module: str) -> dict[str, dict[str, Any]]:
    """settings/fields → api_name anahtarlı canlı metadata."""
    response = _request(
        "GET", "/crm/v7/settings/fields", params={"module": module}
    )
    if response.status_code == 204:
        return {}
    fields = response.json().get("fields") or []
    result: dict[str, dict[str, Any]] = {}
    for field in fields:
        api_name = field.get("api_name")
        if not api_name:
            continue
        data_type = field.get("data_type")
        picklist_values = None
        if data_type in _PICKLIST_TYPES:
            picklist_values = _normalize_picklist(field.get("pick_list_values") or [])
        result[api_name] = {
            "api_name": api_name,
            "field_label": field.get("field_label"),
            "data_type": data_type,
            "is_custom": bool(field.get("custom_field", False)),
            "picklist_values": picklist_values,
        }
    return result


def _normalize_picklist(raw: list[Any]) -> list[dict[str, str | None]]:
    """Picklist seçeneklerini stabil [{display_value, actual_value}] listesine çevirir."""
    items: list[dict[str, str | None]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        display = entry.get("display_value")
        actual = entry.get("actual_value")
        if display is None and actual is None:
            continue
        items.append(
            {
                "display_value": str(display) if display is not None else None,
                "actual_value": str(actual) if actual is not None else None,
            }
        )
    items.sort(
        key=lambda v: (
            v.get("actual_value") or "",
            v.get("display_value") or "",
        )
    )
    return items


def _load_snapshot(module: str) -> dict[str, dict[str, Any]]:
    """zoho_schema_snapshot'tan modül alanlarını okur."""
    query = """
        SELECT api_name, field_label, data_type, is_custom, picklist_values
        FROM zoho_schema_snapshot
        WHERE org_id = %s AND module = %s
    """
    with client.transaction() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(query, (get_org_id(), module)).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        pick = row["picklist_values"]
        if isinstance(pick, list):
            pick = _normalize_picklist(pick)
        result[row["api_name"]] = {
            "api_name": row["api_name"],
            "field_label": row["field_label"],
            "data_type": row["data_type"],
            "is_custom": row["is_custom"],
            "picklist_values": pick,
        }
    return result


def _save_snapshot(module: str, live: dict[str, dict[str, Any]]) -> None:
    """Canlı metadata'yı snapshot'a yazar (modül bazında replace)."""
    org_id = get_org_id()
    delete_sql = """
        DELETE FROM zoho_schema_snapshot
        WHERE org_id = %s AND module = %s
    """
    insert_sql = """
        INSERT INTO zoho_schema_snapshot (
            org_id, module, api_name, field_label, data_type,
            is_custom, picklist_values, captured_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
    """
    with client.transaction() as conn:
        conn.execute(delete_sql, (org_id, module))
        for field in live.values():
            pick = field["picklist_values"]
            conn.execute(
                insert_sql,
                (
                    org_id,
                    module,
                    field["api_name"],
                    field["field_label"],
                    field["data_type"],
                    field["is_custom"],
                    Json(pick) if pick is not None else None,
                ),
            )


def _picklist_key(entry: dict[str, str | None]) -> str:
    """Karşılaştırma anahtarı: actual yoksa display."""
    return entry.get("actual_value") or entry.get("display_value") or ""


def _diff_picklists(
    module: str,
    api_name: str,
    old: list[dict[str, str | None]] | None,
    new: list[dict[str, str | None]] | None,
) -> list[str]:
    """Picklist fark satırları üretir."""
    lines: list[str] = []
    old_list = old or []
    new_list = new or []
    old_by_key = {_picklist_key(v): v for v in old_list if _picklist_key(v)}
    new_by_key = {_picklist_key(v): v for v in new_list if _picklist_key(v)}

    for key in sorted(set(new_by_key) - set(old_by_key)):
        val = new_by_key[key]
        lines.append(
            f"  PICKLIST DEĞERİ EKLENDİ  {module}.{api_name}: "
            f"display={val.get('display_value')!r} actual={val.get('actual_value')!r}"
        )
    for key in sorted(set(old_by_key) - set(new_by_key)):
        val = old_by_key[key]
        lines.append(
            f"  PICKLIST DEĞERİ KALDIRILDI  {module}.{api_name}: "
            f"display={val.get('display_value')!r} actual={val.get('actual_value')!r}"
        )
    for key in sorted(set(old_by_key) & set(new_by_key)):
        o, n = old_by_key[key], new_by_key[key]
        if o.get("display_value") != n.get("display_value") or o.get(
            "actual_value"
        ) != n.get("actual_value"):
            lines.append(
                f"  PICKLIST DEĞERİ DEĞİŞTİ  {module}.{api_name}: "
                f"{o.get('display_value')!r}/{o.get('actual_value')!r} → "
                f"{n.get('display_value')!r}/{n.get('actual_value')!r}"
            )
    return lines


def _diff_module(
    module: str,
    snapshot: dict[str, dict[str, Any]],
    live: dict[str, dict[str, Any]],
) -> list[str]:
    """Alan ve picklist farklarını listeler."""
    lines: list[str] = []
    snap_names = set(snapshot)
    live_names = set(live)

    for api_name in sorted(live_names - snap_names):
        field = live[api_name]
        lines.append(
            f"  YENİ ALAN  {module}.{api_name} "
            f"(label={field.get('field_label')!r}, type={field.get('data_type')!r})"
        )
    for api_name in sorted(snap_names - live_names):
        lines.append(f"  SİLİNMİŞ ALAN  {module}.{api_name}")

    for api_name in sorted(snap_names & live_names):
        old, new = snapshot[api_name], live[api_name]
        if old.get("data_type") != new.get("data_type"):
            lines.append(
                f"  TİP DEĞİŞTİ  {module}.{api_name}: "
                f"{old.get('data_type')!r} → {new.get('data_type')!r}"
            )
        lines.extend(
            _diff_picklists(
                module,
                api_name,
                old.get("picklist_values"),
                new.get("picklist_values"),
            )
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoho CRM şema drift kontrolü.")
    parser.add_argument(
        "--module",
        required=True,
        help="virgülle ayrılmış modül API adları (örn. Calls,Leads)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="karşılaştırmadan sonra snapshot'ı canlı metadata ile güncelle",
    )
    args = parser.parse_args()

    modules = [m.strip() for m in args.module.split(",") if m.strip()]
    if not modules:
        print("--module boş")
        return 1

    load_dotenv()

    any_diff = False
    for module in modules:
        print(f"=== {module} ===")
        try:
            live = _fetch_live_fields(module)
        except (ZohoAuthError, ZohoCrmError) as exc:
            print(f"  HATA — canlı metadata: {exc}")
            return 1

        try:
            snapshot = _load_snapshot(module)
        except Exception as exc:
            print(f"  HATA — snapshot okuma: {exc}")
            return 1

        if not snapshot:
            print(f"  snapshot yok ({len(live)} canlı alan)")
            if args.save:
                _save_snapshot(module, live)
                print("  snapshot kaydedildi (ilk kez)")
            else:
                print("  --save ile ilk snapshot alınabilir")
                any_diff = True
            print()
            continue

        diffs = _diff_module(module, snapshot, live)
        if diffs:
            any_diff = True
            print(f"  {len(diffs)} fark:")
            for line in diffs:
                print(line)
        else:
            print("  fark yok")

        if args.save:
            _save_snapshot(module, live)
            print("  snapshot güncellendi")
        print()

    return 1 if any_diff else 0


if __name__ == "__main__":
    sys.exit(main())
