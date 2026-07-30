"""Zoho CRM modülünden birkaç kayıt çekip tüm alanları basar.

Alan adlarını doğrulamak için kullanılır: gerçek kayıtlardaki API
isimlerini ve örnek değerleri gösterir. Canlı ortamda çalıştırılır;
credential yoksa çalıştırılmaz.

Kullanım:
    python scripts/inspect_zoho_module.py --module Calls --limit 3

.env otomatik yüklenir.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Script doğrudan çalıştırıldığında pusula paketinin bulunabilmesi için
# repo kökünü path'e ekle.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.zoho import ZohoAuthError, ZohoCrmError
from pusula.zoho.crm import _request


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zoho CRM modülünden örnek kayıtların alanlarını basar."
    )
    parser.add_argument("--module", required=True, help="CRM modül API adı (örn. Calls)")
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="çekilecek kayıt sayısı (varsayılan: 3)",
    )
    args = parser.parse_args()

    if args.limit < 1:
        print("--limit en az 1 olmalı")
        return 1

    load_dotenv()

    try:
        response = _request(
            "GET",
            f"/crm/v7/{args.module}",
            params={"per_page": args.limit},
        )
    except (ZohoAuthError, ZohoCrmError) as exc:
        print(f"{args.module}: HATA — {exc}")
        return 1

    if response.status_code in (204, 304):
        print(f"{args.module}: kayıt yok (HTTP {response.status_code})")
        return 0

    records = response.json().get("data") or []
    print(f"{args.module}: {len(records)} kayıt\n")
    for index, record in enumerate(records, start=1):
        print(f"--- kayıt {index} ---")
        # Alan adlarını alfabetik bas; iç içe yapılar JSON olarak okunur.
        for key in sorted(record.keys()):
            print(f"  {key}: {json.dumps(record[key], ensure_ascii=False, default=str)}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
