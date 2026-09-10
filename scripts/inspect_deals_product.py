"""Zoho Deals ürün alanı keşfi. DB'ye yazmaz.

Kullanım:
    python scripts/inspect_deals_product.py

.env otomatik yüklenir. Metadata + bir örnek kaydın ham alanları
basılır; aday alanda COQL ile değer × adet sayılır.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Sequence
from itertools import islice
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request, coql, get_records

_MAX_FIELDS = 50
_HINTS = ("premium", "plus", "kamp")
_NAME_HINTS = (
    "product",
    "urun",
    "ürün",
    "paket",
    "package",
    "kamp",
    "premium",
    "plan",
)


def _chunks(items: Sequence[str], size: int) -> list[list[str]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _load_fields() -> list[dict[str, Any]]:
    response = _request(
        "GET", "/crm/v7/settings/fields", params={"module": "Deals"}
    )
    if response.status_code == 204:
        return []
    return list(response.json().get("fields") or [])


def _load_related_lists() -> list[dict[str, Any]]:
    response = _request(
        "GET", "/crm/v7/settings/related_lists", params={"module": "Deals"}
    )
    if response.status_code == 204:
        return []
    return list(response.json().get("related_lists") or [])


def _picklist_values(field: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in field.get("pick_list_values") or []:
        if not isinstance(entry, dict):
            continue
        display = entry.get("display_value")
        actual = entry.get("actual_value")
        if display is None and actual is None:
            continue
        if display is not None and actual is not None and str(display) != str(actual):
            out.append(f"{display!s} ({actual!s})")
        else:
            out.append(str(display if display is not None else actual))
    return out


def _name_hit(api_name: str, label: str) -> bool:
    blob = f"{api_name} {label}".lower()
    return any(h in blob for h in _NAME_HINTS)


def _value_hit(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str).lower()
    return any(h in text for h in _HINTS)


def _display_value(raw: Any) -> str:
    if raw is None or raw == "":
        return "<bos>"
    if isinstance(raw, list):
        if not raw:
            return "<bos>"
        return " | ".join(_display_value(item) for item in raw)
    if isinstance(raw, dict):
        return str(raw.get("name") or raw.get("id") or raw)
    return str(raw)


def _get_by_id(deal_id: str, api_names: Sequence[str]) -> dict[str, Any]:
    names = list(api_names)
    if "id" not in names:
        names = ["id", *names]
    merged: dict[str, Any] = {"id": deal_id}
    for chunk in _chunks(names, _MAX_FIELDS):
        fields = list(chunk)
        if "id" not in fields:
            fields = ["id", *fields][:_MAX_FIELDS]
        response = _request(
            "GET",
            f"/crm/v7/Deals/{deal_id}",
            params={"fields": ",".join(fields)},
        )
        if response.status_code == 204:
            continue
        data = response.json().get("data") or []
        if data:
            merged.update(data[0])
    return merged


def _first_deal_id() -> str | None:
    for raw in islice(get_records("Deals", ["id"], per_page=1), 1):
        rid = raw.get("id")
        if rid:
            return str(rid)
    return None


def _first_filled_id(api_name: str) -> str | None:
    query = (
        f"select id, {api_name}, Created_Time from Deals "
        f"where {api_name} is not null order by Created_Time desc"
    )
    try:
        for record in coql(query):
            rid = record.get("id")
            if rid:
                return str(rid)
    except (ZohoCrmError, ValueError):
        return None
    return None


def _lookup_module(field: dict[str, Any]) -> str | None:
    lookup = field.get("lookup")
    if not isinstance(lookup, dict):
        return None
    module = lookup.get("module")
    if isinstance(module, dict):
        name = str(module.get("api_name") or "")
        return name or None
    if isinstance(module, str) and module:
        return module
    return None


def _coql_counts(api_names: Sequence[str]) -> dict[str, list[tuple[str, int]]]:
    counters: dict[str, Counter[str]] = {name: Counter() for name in api_names}
    select_list = ", ".join(["id", *api_names])
    # COQL where zorunlu; lookup nesnesi id+name olarak gelir.
    query = (
        f"select {select_list} from Deals "
        "where id is not null order by Created_Time asc"
    )
    for record in coql(query):
        for name in api_names:
            counters[name][_display_value(record.get(name))] += 1
    return {name: counters[name].most_common() for name in api_names}


def _fetch_related(deal_id: str, related: str) -> list[dict[str, Any]]:
    page = 1
    rows: list[dict[str, Any]] = []
    while True:
        try:
            response = _request(
                "GET",
                f"/crm/v7/Deals/{deal_id}/{related}",
                params={
                    "page": page,
                    "per_page": 200,
                    "fields": "id,Product_Name,Product_Code",
                },
            )
        except ZohoCrmError as exc:
            print(f"iliskili {related} HATA: {exc}")
            return rows
        if response.status_code == 204:
            return rows
        payload = response.json()
        rows.extend(payload.get("data") or [])
        info = payload.get("info") or {}
        if not info.get("more_records"):
            return rows
        page += 1


def main() -> int:
    load_dotenv()
    try:
        fields = _load_fields()
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"metadata HATA: {exc}")
        return 1
    print(f"Deals metadata: {len(fields)} alan")
    print("api_name | field_label | data_type | custom")
    name_hits: list[str] = []
    pick_hits: list[str] = []
    by_api: dict[str, dict[str, Any]] = {}
    for field in sorted(fields, key=lambda f: str(f.get("api_name") or "")):
        api_name = str(field.get("api_name") or "")
        if not api_name:
            continue
        by_api[api_name] = field
        label = str(field.get("field_label") or "")
        data_type = str(field.get("data_type") or "")
        custom = "yes" if field.get("custom_field") else "no"
        print(f"{api_name} | {label} | {data_type} | {custom}")
        if _name_hit(api_name, label):
            name_hits.append(api_name)
            module = _lookup_module(field)
            if module:
                print(f"  lookup modul: {module}")
        picks = _picklist_values(field)
        if picks:
            joined = " / ".join(picks)
            print(f"  picklist: {joined}")
            blob = joined.lower()
            if any(h in blob for h in _HINTS):
                pick_hits.append(api_name)

    print(f"ad ipucu alanlar: {name_hits or 'yok'}")
    print(f"picklist ipucu alanlar: {pick_hits or 'yok'}")

    try:
        related_lists = _load_related_lists()
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"iliskili listeler HATA: {exc}")
        related_lists = []
    related_hits: list[str] = []
    print("iliskili listeler:")
    for item in related_lists:
        api_name = str(item.get("api_name") or "")
        label = str(item.get("display_label") or item.get("module") or "")
        print(f"  {api_name} | {label}")
        if _name_hit(api_name, label):
            related_hits.append(api_name)
    print(f"iliskili urun ipucu: {related_hits or 'yok'}")

    candidates = list(dict.fromkeys(pick_hits + name_hits))
    sample_id: str | None = None
    for api_name in candidates:
        sample_id = _first_filled_id(api_name)
        if sample_id:
            print(f"dolu ornek arandi {api_name} id={sample_id}")
            break
    if sample_id is None:
        try:
            sample_id = _first_deal_id()
        except (ZohoAuthError, ZohoCrmError) as exc:
            print(f"ornek kayit HATA: {exc}")
            return 1
        print(f"ilk kayit id={sample_id}")
    if not sample_id:
        print("ornek kayit yok")
        return 1

    api_names = list(by_api)
    try:
        sample = _get_by_id(sample_id, api_names)
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"ornek kayit HATA: {exc}")
        return 1

    print(f"ornek kayit id={sample.get('id')}")
    hits: list[str] = []
    for key, value in sorted(sample.items()):
        if value is None or value == "" or value == [] or value == {}:
            continue
        if _value_hit(value) or key in name_hits or key in pick_hits:
            hits.append(key)
            print(f"HAM {key}={json.dumps(value, ensure_ascii=False, default=str)}")

    if not hits:
        print("ornekte Premium/Plus/Kamp eslesmesi yok; dolu picklist/lookup:")
        for key, value in sorted(sample.items()):
            data_type = str((by_api.get(key) or {}).get("data_type") or "")
            if data_type not in {"picklist", "multiselectpicklist", "lookup"}:
                continue
            if value is None or value == "" or value == []:
                continue
            print(f"HAM {key}={json.dumps(value, ensure_ascii=False, default=str)}")

    for related in related_hits:
        rows = _fetch_related(sample_id, related)
        print(f"iliskili {related} adet={len(rows)}")
        for row in rows[:3]:
            print(f"HAM {related}={json.dumps(row, ensure_ascii=False, default=str)}")

    # Deal_Name icindeki Premium eslesmesi sayima girmez; aday alanlar.
    count_fields = [
        name for name in dict.fromkeys(pick_hits + name_hits) if name in by_api
    ]
    if not count_fields:
        print("sayilacak urun alani yok")
        return 0
    try:
        distributions = _coql_counts(count_fields)
    except (ZohoAuthError, ZohoCrmError, ValueError) as exc:
        print(f"dagilim HATA: {exc}")
        return 1
    for api_name, rows in distributions.items():
        print(f"dagilim {api_name}:")
        total = 0
        for value, n in rows:
            print(f"  {value} | {n}")
            total += n
        print(f"  toplam={total}")
        module = _lookup_module(by_api[api_name])
        if not module:
            continue
        try:
            _print_lookup_catalog(module)
        except (ZohoAuthError, ZohoCrmError, ValueError) as exc:
            print(f"katalog {module} HATA: {exc}")
    return 0


def _print_lookup_catalog(module: str) -> None:
    response = _request(
        "GET", "/crm/v7/settings/fields", params={"module": module}
    )
    if response.status_code == 204:
        print(f"katalog {module}: metadata bos")
        return
    api_names = {
        str(f.get("api_name") or "")
        for f in (response.json().get("fields") or [])
        if f.get("api_name")
    }
    name_field = "Product_Name" if "Product_Name" in api_names else "Name"
    if name_field not in api_names:
        print(f"katalog {module}: isim alani yok {sorted(api_names)[:12]}")
        return
    query = f"select id, {name_field} from {module} where id is not null"
    print(f"katalog {module} alan={name_field}:")
    n = 0
    for record in coql(query):
        n += 1
        name = _display_value(record.get(name_field))
        print(f"  {name} | id={record.get('id')}")
    print(f"  katalog_toplam={n}")


if __name__ == "__main__":
    sys.exit(main())
