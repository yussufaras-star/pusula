"""Finans senkronu — ECB kurları + Mercury / Wise / Stripe / Revolut hesapları.

Kullanım:
    python scripts/finance_sync.py
    python scripts/finance_sync.py --account 3 --days 30

GitHub Actions'ta sağlayıcı anahtarları FIN_SECRETS_JSON (toJSON(secrets))
içinden okunur; yalnız MERCURY_TOKEN_*, WISE_TOKEN_*, STRIPE_KEY_*,
REVOLUT_*_* adları ortama alınır.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula.finance.sync import load_provider_secrets, run_finance_sync


def main() -> int:
    parser = argparse.ArgumentParser(description="Finans senkronu.")
    parser.add_argument("--account", type=int, default=None, help="Yalnız bu hesap (fin_accounts.id)")
    parser.add_argument("--days", type=int, default=None, help="Geriye bakış (gün); boşsa son senkrondan")
    parser.add_argument("--budget", type=float, default=240.0, help="Hesap başlatma süresi (sn)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    load_dotenv()
    loaded = load_provider_secrets(os.environ.pop("FIN_SECRETS_JSON", None))
    if loaded:
        print(f"saglayici anahtarlari: {', '.join(loaded)}")

    report = run_finance_sync(account_id=args.account, days=args.days, budget_sec=args.budget)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if "skipped" in report:
        return 0
    failed = [str(r.get("name")) for r in report.get("accounts", []) if r.get("error")]
    if not report.get("fx", {}).get("ok"):
        failed.append("kurlar")
    if failed:
        print(f"hata: basarisiz: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
