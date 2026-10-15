"""Günlük kart görselleri — prim kartı ve temsilci tablosu ekip Cliq kanalına.

Paneli yerelde açar (uvicorn + derlenmiş arayüz), tek seferlik yönetici
oturumuyla kartların ekran görüntüsünü alır ve kanala gönderir.
Önce: npm --prefix web run build. Varsayılan gönderir; --out DIR yalnız kaydeder.
--cards prim,temsilciler hangi kartların gideceğini seçer (öğlen yalnız temsilciler).
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
# Görsele girmeyenler; web/src/components/ShareButton.tsx ile aynı.
SKIP = "[data-share-skip], .link-btn, .cm-orphans"
CARDS = [("prim", "Aylık prim hedefi"), ("temsilciler", "Temsilciler")]


def _wait_up(proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Panel sunucusu kapandı")
        try:
            urllib.request.urlopen(f"{BASE}/api/health", timeout=2)
            return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("Panel sunucusu açılmadı")


def capture(out: Path | None, cards: list[tuple[str, str]]) -> None:
    from playwright.sync_api import sync_playwright

    from pusula.card_share import post_channel_image
    from pusula.metrics import TZ
    from pusula.panel_auth import ADMIN_EMAIL, AuthUser
    from pusula.web.auth import COOKIE_NAME, encode_session

    # Bu çalıştırmaya özel imza anahtarı: oturum yalnız bu yerel sunucuda geçer.
    os.environ["PUSULA_SESSION_SECRET"] = secrets.token_urlsafe(48)
    os.environ.pop("VERCEL", None)
    cookie = encode_session(AuthUser(email=ADMIN_EMAIL, role="admin", rep_id=None, full_name="Pusula"))
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "pusula.web.app:app", "--port", str(PORT), "--log-level", "warning"],
        env=os.environ.copy(),
    )
    try:
        _wait_up(server)
        stamp = datetime.now(TZ).strftime("%d.%m %H:%M")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                device_scale_factor=2,
                color_scheme="light",
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
            )
            context.add_cookies([{"name": COOKIE_NAME, "value": cookie, "url": BASE}])
            page = context.new_page()
            page.goto(f"{BASE}/#/canli")
            for kart, _ in cards:
                page.wait_for_selector(f"[data-share='{kart}'] table", timeout=90_000)
            page.wait_for_load_state("networkidle")
            page.wait_for_function("!document.querySelector('[data-share] .skeleton')", timeout=60_000)
            page.add_style_tag(content=f"{SKIP}{{display:none !important}}")
            for kart, baslik in cards:
                png = page.locator(f"[data-share='{kart}']").screenshot()
                if out:
                    out.mkdir(parents=True, exist_ok=True)
                    (out / f"{kart}.png").write_bytes(png)
                    print(f"{kart}: {len(png)} bayt kaydedildi")
                else:
                    ids = post_channel_image(png, f"pusula-{kart}.png", f"{baslik} · {stamp}")
                    print(f"{kart}: kanala gönderildi {ids}")
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Göndermeden bu klasöre kaydet")
    parser.add_argument("--cards", default=",".join(k for k, _ in CARDS), help="Virgülle kart adları")
    args = parser.parse_args()
    wanted = {c.strip() for c in args.cards.split(",") if c.strip()}
    unknown = wanted - {k for k, _ in CARDS}
    if unknown or not wanted:
        parser.error(f"Bilinmeyen kart: {', '.join(sorted(unknown)) or 'boş'}")
    capture(args.out, [c for c in CARDS if c[0] in wanted])


if __name__ == "__main__":
    main()
