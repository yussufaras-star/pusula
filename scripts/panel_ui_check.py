"""Panel ekran doğrulaması — AppTest; isteğe bağlı Playwright.

Kullanım:
    python scripts/panel_ui_check.py
    python scripts/panel_ui_check.py --shots
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from pusula.config import get_org_id
from pusula.panel_auth import ADMIN_EMAIL
from pusula.panel_ciro import AFTER_SALES_IDS, SALES_TEAM_IDS
from pusula.panel_data import connect

ROOT = Path(__file__).resolve().parent.parent
PANEL = ROOT / "app" / "panel.py"
SHOT_DIR = Path("/tmp/pusula-panel-shots")
VERIFY_PASSWORD = "verify-ci"


def _roster() -> list[dict[str, str]]:
    org_id = get_org_id()
    ids = list(SALES_TEAM_IDS) + list(AFTER_SALES_IDS)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT rep_id, full_name, email
            FROM reps
            WHERE org_id = %s AND rep_id = ANY(%s)
            """,
            (org_id, ids),
        ).fetchall()
    out: list[dict[str, str]] = []
    for rep_id, name, email in rows:
        out.append(
            {
                "rep_id": str(rep_id),
                "full_name": str(name),
                "email": str(email or ""),
            }
        )
    return out


def _sales_one(roster: list[dict[str, str]]) -> dict[str, str]:
    by_id = {row["rep_id"]: row for row in roster}
    for rid in SALES_TEAM_IDS:
        row = by_id.get(rid)
        if row is not None and row["email"]:
            return row
    raise RuntimeError("aktif satis temsilcisi e-posta yok")


def _help_of(el: Any) -> str:
    direct = getattr(el, "help", None)
    if direct:
        return str(direct)
    proto = getattr(el, "proto", None)
    if proto is None:
        return ""
    return str(getattr(proto, "help", "") or "")


def _dump(at: Any, title: str) -> str:
    lines: list[str] = [f"=== {title} ==="]
    lines.append(f"exception={at.exception}")
    lines.append(
        "tabs=" + ",".join(t.label for t in at.tabs) if at.tabs else "tabs=(yok)"
    )
    lines.append(
        "date_input="
        + ",".join(f"{d.label}:{d.value}" for d in at.date_input)
    )
    lines.append(
        "radio=" + ",".join(getattr(r, "label", "") for r in at.radio)
    )
    lines.append(
        "selectbox="
        + " | ".join(
            f"{s.label} options={list(s.options)} value={s.value}"
            for s in at.selectbox
        )
    )
    lines.append(
        "expander=" + ",".join(e.label for e in at.expander)
    )
    lines.append("titles=" + " | ".join(t.value for t in at.title))
    lines.append("subheaders=" + " | ".join(s.value for s in at.subheader))
    mayis_helps: list[str] = []
    for cap in at.caption:
        help_text = _help_of(cap)
        if "Mayıs" in help_text or "Mayis" in help_text:
            mayis_helps.append(help_text)
    lines.append("caption_mayis_help=" + (" | ".join(mayis_helps) or "(yok)"))
    lines.append("captions:")
    for cap in at.caption[:80]:
        lines.append(f"  {cap.value}")
    if len(at.caption) > 80:
        lines.append(f"  ... +{len(at.caption) - 80} caption")
    lines.append("markdown:")
    for md in at.markdown[:40]:
        lines.append(f"  {md.value}")
    if len(at.markdown) > 40:
        lines.append(f"  ... +{len(at.markdown) - 40} markdown")
    ekip_lines = [
        c.value for c in at.caption if str(c.value).startswith("ekip ")
    ]
    lines.append(f"ekip_satir_sayisi={len(ekip_lines)}")
    for line in ekip_lines[:12]:
        lines.append(f"  {line}")
    return "\n".join(lines)


def _all_text(at: Any) -> str:
    chunks: list[str] = []
    for attr in ("title", "header", "subheader", "markdown", "caption", "text"):
        for el in getattr(at, attr, []):
            chunks.append(str(getattr(el, "value", "")))
            chunks.append(_help_of(el))
    for box in at.selectbox:
        chunks.append(box.label)
        chunks.extend(str(opt) for opt in box.options)
        chunks.append(str(box.value))
    for exp in at.expander:
        chunks.append(exp.label)
    for tab in at.tabs:
        chunks.append(tab.label)
    for frame in at.dataframe:
        try:
            chunks.append(str(frame.value))
        except Exception:
            pass
    return "\n".join(chunks)


def _run_app(session: dict[str, Any], timeout: float = 240.0) -> Any:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(PANEL), default_timeout=timeout)
    for key, value in session.items():
        at.session_state[key] = value
    at.run()
    return at


def _assert_common(at: Any, *, admin: bool) -> list[str]:
    errors: list[str] = []
    if at.exception is not None:
        errors.append(f"exception: {at.exception}")
    tab_labels = [t.label for t in at.tabs]
    if admin:
        if tab_labels != ["Yönetici", "Temsilci"]:
            errors.append(f"admin sekmeler {tab_labels}")
    elif tab_labels:
        errors.append(f"temsilci sekmeler {tab_labels}")
    date_labels = [d.label for d in at.date_input]
    if date_labels != ["Gün (bloklar)"]:
        errors.append(f"tarih secici {date_labels}")
    radio_labels = [getattr(r, "label", "") for r in at.radio]
    joined = " ".join(radio_labels)
    if "30" in joined or "60" in joined or "serbest" in joined:
        errors.append(f"aralik radio {radio_labels}")
    captions = [str(c.value).strip() for c in at.caption]
    if "dönüşüm 1 Mayıs 2026 sonrası" in captions:
        errors.append("baslikta 1 Mayis satiri var")
    sub = [str(s.value) for s in at.subheader]
    if any(s.startswith("Saatlik") for s in sub):
        errors.append(f"saatlik subheader {sub}")
    return errors


def main() -> int:
    shots = "--shots" in sys.argv
    os.chdir(ROOT)
    roster = _roster()
    person = _sales_one(roster)
    print(f"kilitli temsilci={person['full_name']} {person['email']}")

    t0 = time.perf_counter()
    at_rep = _run_app(
        {
            "auth_email": person["email"],
            "auth_role": "rep",
            "auth_rep_id": person["rep_id"],
            "auth_name": person["full_name"],
        }
    )
    rep_s = time.perf_counter() - t0
    print(_dump(at_rep, "temsilci girisi"))
    print(f"temsilci AppTest: {rep_s:.2f}s")

    errors = _assert_common(at_rep, admin=False)
    blob = _all_text(at_rep)
    if "ekip " not in blob:
        errors.append("temsilcide ekip ortalamasi yok")
    others = [
        row["full_name"]
        for row in roster
        if row["rep_id"] != person["rep_id"]
    ]
    leaked = [name for name in others if name and name in blob]
    if leaked:
        errors.append(f"baska temsilci adi: {leaked}")
    if at_rep.selectbox:
        errors.append("temsilcide selectbox var")

    t0 = time.perf_counter()
    at_admin = _run_app(
        {
            "auth_email": ADMIN_EMAIL,
            "auth_role": "admin",
            "auth_rep_id": None,
            "auth_name": None,
        }
    )
    admin_s = time.perf_counter() - t0
    print(_dump(at_admin, "yonetici girisi (tumu)"))
    print(f"yonetici AppTest: {admin_s:.2f}s")
    errors.extend(_assert_common(at_admin, admin=True))
    help_blob = " ".join(_help_of(c) for c in at_admin.caption)
    help_blob += " ".join(_help_of(s) for s in at_admin.subheader)
    if "1 Mayıs 2026" not in help_blob and "1 Mayis 2026" not in help_blob:
        errors.append("tazelik (?) balonunda 1 Mayis yok")

    if at_admin.selectbox:
        box = at_admin.selectbox[0]
        if len(box.options) > 1:
            box.select_index(1)
            at_admin.run()
            print(_dump(at_admin, "yonetici girisi (bir temsilci)"))
            if "ekip " not in _all_text(at_admin):
                errors.append("yonetici temsilci seciminde ekip yok")

    expanders = [e.label for e in at_admin.expander]
    if "saat kırılımı" not in expanders:
        errors.append(f"saat kirilimi expander yok: {expanders}")
    else:
        first = next(e for e in at_admin.expander if e.label == "saat kırılımı")
        hour_caps = [
            c.value for c in first.caption if ":00" in str(c.value)
        ]
        print("kirilim saat caption:")
        for line in hour_caps[:12]:
            print(f"  {line}")
        yetersiz = [
            c.value
            for c in first.caption
            if "veri yetersiz" in str(c.value)
        ]
        print(f"kirilim veri yetersiz hucre={len(yetersiz)}")
        for line in yetersiz[:6]:
            print(f"  {line}")

    print(f"AppTest sureleri temsilci={rep_s:.2f}s yonetici={admin_s:.2f}s")
    if errors:
        print("hata:")
        for err in errors:
            print(f"  {err}")
        return 1
    print("panel_ui_check: ok")
    if shots:
        return _shots(person)
    return 0


def _write_ci_secrets(person: dict[str, str]) -> Path:
    home = Path("/tmp/pusula-st-home")
    dest = home / ".streamlit" / "secrets.toml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        "[passwords]\n"
        f'"{ADMIN_EMAIL}" = "{VERIFY_PASSWORD}"\n'
        f'"{person["email"]}" = "{VERIFY_PASSWORD}"\n',
        encoding="utf-8",
    )
    return home


def _shots(person: dict[str, str]) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright yok, ekran goruntusu atlandi")
        return 0
    import subprocess

    home = _write_ci_secrets(person)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(PANEL),
            "--server.headless",
            "true",
            "--server.port",
            "8501",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    ready = False
    assert proc.stdout is not None
    t_end = time.time() + 90
    buf: list[str] = []
    while time.time() < t_end:
        line = proc.stdout.readline()
        if line:
            buf.append(line)
            if "You can now view" in line or "Local URL" in line:
                ready = True
                break
        elif proc.poll() is not None:
            break
    if not ready:
        print("streamlit ayaga kalkmadi")
        print("".join(buf[-20:]))
        proc.kill()
        return 1
    time.sleep(2)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:8501", wait_until="domcontentloaded")
            page.get_by_label("Kullanıcı adı").fill(person["email"])
            page.get_by_label("Şifre").fill(VERIFY_PASSWORD)
            page.get_by_role("button", name="Giriş").click()
            page.wait_for_timeout(8000)
            page.screenshot(
                path=str(SHOT_DIR / "temsilci.png"), full_page=True
            )
            loc = page.get_by_text("saat kırılımı")
            if loc.count() > 0:
                loc.first.click()
                page.wait_for_timeout(1500)
                page.screenshot(
                    path=str(SHOT_DIR / "temsilci-kirilim.png"),
                    full_page=True,
                )
            if page.get_by_role("button", name="Çıkış").count() > 0:
                page.get_by_role("button", name="Çıkış").click()
                page.wait_for_timeout(2000)
            page.get_by_label("Kullanıcı adı").fill(ADMIN_EMAIL)
            page.get_by_label("Şifre").fill(VERIFY_PASSWORD)
            page.get_by_role("button", name="Giriş").click()
            page.wait_for_timeout(12000)
            page.screenshot(
                path=str(SHOT_DIR / "yonetici.png"), full_page=True
            )
            tabs = page.get_by_role("tab")
            print(f"playwright tab sayisi={tabs.count()}")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("shots:")
    for path in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {path} {path.stat().st_size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
