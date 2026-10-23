"""Günlük CEO özeti (satış): hesaplar, kaydeder, Cliq'ten gönderir.

Kullanım:
    python scripts/ceo_summary.py                      # yalnız yazdır
    python scripts/ceo_summary.py --send               # kaydet ve CEO'ya gönder (günde bir kez)
    python scripts/ceo_summary.py --send --to e@rexven.com   # başka alıcı (deneme)

Hesap ~1 dk sürer; panel kaydedilen özeti gösterir.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from pusula import ceo_summary
from pusula.block_share import apply_shadow, post_cliq
from pusula.metrics import TZ
from pusula.panel_ciro import is_workday
from pusula.panel_data import connect


def main() -> int:
    parser = argparse.ArgumentParser(description="Günlük CEO özeti.")
    parser.add_argument("--send", action="store_true", help="Kaydet ve Cliq'ten gönder")
    parser.add_argument("--to", default=None, help=f"Alıcı (varsayılan {ceo_summary.RECIPIENT})")
    parser.add_argument("--day", default=None, help="Bugün yerine (YYYY-AA-GG; deneme)")
    parser.add_argument("--force", action="store_true", help="Bugün gönderildiyse de gönder")
    parser.add_argument("--channel", default=None, help="Görsel kartın gideceği Cliq kanalı (benzersiz ad)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    load_dotenv()
    now = datetime.now(TZ)
    today = date.fromisoformat(args.day) if args.day else now.date()
    if args.send and not is_workday(today):
        print("Pazar: özet gönderilmez.")
        return 0
    with connect() as conn:
        if args.send and not args.force and ceo_summary.sent_today(conn, today):
            print("Bugünün özeti zaten gönderildi.")
            return 0
    summary = ceo_summary.build_summary(today, now)
    body = ceo_summary.render(summary)
    print(body)
    if not args.send:
        return 0
    # Virgülle birden çok alıcı (CEO ve yönetici).
    to = ",".join(a.strip() for a in (args.to or ceo_summary.RECIPIENT).split(",") if a.strip())
    note, card, slides = ceo_summary.cliq_card(summary)
    with connect() as conn:
        ceo_summary.save(conn, summary, body)
        if args.channel:
            # Bot kişiye görsel gönderemiyor; görsel kart yalnız CEO özeti kanalına gider.
            from pusula import ceo_card
            from pusula.card_share import post_channel_image

            png = ceo_card.render_png(ceo_card.render_html(summary))
            try:
                post_channel_image(png, f"yonetici-ozeti-{today}.png", card["title"], channel=args.channel)
            except Exception as exc:  # noqa: BLE001 — kanal reddederse yöneticiye haber ver
                logging.exception("Kanala gönderilemedi")
                post_cliq(
                    f"Yönetici özeti #{args.channel} kanalına gönderilemedi ({exc}). Bot kanalda üye mi?\n\n{body}",
                    ceo_summary.RECIPIENT,
                )
                return 1
            ceo_summary.mark_sent(conn, today, f"#{args.channel}")
            print(f"Gönderildi: #{args.channel}")
            return 0
        text, shadow_to, shadow = apply_shadow(note, to)
        try:
            post_cliq(text, shadow_to if shadow else to, card=card, slides=slides)
        except Exception as exc:  # noqa: BLE001 — kart reddedilirse düz metin gider
            logging.warning("Cliq kartı gitmedi, metin gönderiliyor: %s", exc)
            text, _, _ = apply_shadow(body, to)
            post_cliq(text, shadow_to if shadow else to)
        ceo_summary.mark_sent(conn, today, shadow_to if shadow else to)
    print(f"Gönderildi: {shadow_to if shadow else to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
