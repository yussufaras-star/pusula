"""Finans Yönetici Özeti: hesaplar, kaydeder, görsel kartı Cliq kanalına gönderir.

Kullanım:
    python scripts/finance_summary.py                              # yalnız yazdır
    python scripts/finance_summary.py --send --channel headquarters  # kaydet ve gönder (günde bir kez)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula import ceo_summary, finance_summary
from pusula.block_share import post_cliq
from pusula.metrics import TZ
from pusula.panel_ciro import is_workday
from pusula.panel_data import connect


def main() -> int:
    parser = argparse.ArgumentParser(description="Finans yönetici özeti.")
    parser.add_argument("--send", action="store_true", help="Kaydet ve Cliq kanalına gönder")
    parser.add_argument("--channel", default=None, help="Cliq kanalının benzersiz adı")
    parser.add_argument("--day", default=None, help="Bugün yerine (YYYY-AA-GG; deneme)")
    parser.add_argument("--force", action="store_true", help="Bugün gönderildiyse de gönder")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    now = datetime.now(TZ)
    today = date.fromisoformat(args.day) if args.day else now.date()
    table = finance_summary.TABLE
    if args.send and not is_workday(today):
        print("Pazar / resmi tatil: özet gönderilmez.")
        return 0
    with connect() as conn:
        if args.send and not args.force and ceo_summary.sent_today(conn, today, table):
            print("Bugünün özeti zaten gönderildi.")
            return 0
        summary = json.loads(json.dumps(finance_summary.build_summary(conn, today, now), default=str))
        body = finance_summary.render(summary)
        print(body)
        if not args.send:
            return 0
        if not args.channel:
            print("--channel gerekli")
            return 2
        ceo_summary.save(conn, summary, body, table)
        from pusula import finance_card
        from pusula.card_share import post_channel_image

        png = finance_card.render_png(finance_card.render_html(summary))
        title = f"{finance_summary.TITLE} · {finance_summary.period_label(summary)}"
        try:
            post_channel_image(png, f"finans-ozeti-{today}.png", title, channel=args.channel)
        except Exception as exc:  # noqa: BLE001 — kanal reddederse yöneticiye haber ver
            logging.exception("Kanala gönderilemedi")
            post_cliq(
                f"Finans özeti #{args.channel} kanalına gönderilemedi: {exc}. "
                "Pusula botunun kanalda üye olduğunu kontrol edin; özet panelde (Finans → Yönetici özeti).",
                ceo_summary.RECIPIENT,
            )
            return 1
        ceo_summary.mark_sent(conn, today, f"#{args.channel}", table)
    print(f"Gönderildi: #{args.channel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
