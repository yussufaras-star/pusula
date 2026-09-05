"""Panel ekran doğrulaması — AppTest; isteğe bağlı Playwright.

Kullanım:
    python scripts/panel_ui_check.py
    python scripts/panel_ui_check.py --shots
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, timedelta
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
    exc = list(at.exception)
    lines.append("exception=" + (", ".join(str(x) for x in exc) if exc else "(yok)"))
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
    lines.append(
        "checkbox=" + ",".join(getattr(c, "label", "") for c in at.checkbox)
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
    lines.append("dataframes:")
    for i, frame in enumerate(at.dataframe[:6]):
        try:
            value = frame.value
            lines.append(f"  df{i} cols={list(value.columns)} rows={len(value)}")
            lines.append(str(value)[:1200])
        except Exception as exc:
            lines.append(f"  df{i} hata={exc}")
    ekip_lines = [
        c.value for c in at.caption if "ekip " in str(c.value)
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
    if len(at.exception) > 0:
        errors.append(f"exception: {list(at.exception)}")
    tab_labels = [t.label for t in at.tabs]
    if admin:
        if tab_labels != ["Yönetici", "Temsilci"]:
            errors.append(f"admin sekmeler {tab_labels}")
    elif tab_labels:
        errors.append(f"temsilci sekmeler {tab_labels}")
    date_labels = [d.label for d in at.date_input]
    if date_labels != ["Gün"]:
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
    if "Ciro" not in sub:
        errors.append(f"Ciro bolumu yok: {sub}")
    blob = _all_text(at)
    if "Kullanıcı adı" in blob or (hasattr(at, "form") and list(at.form)):
        errors.append("giris formu oturumda gorunuyor")
    if "son ingest" not in blob:
        errors.append("ingest gostergesi yok")
    if "11:15 çalışmadı" in blob or "14:15 çalışmadı" in blob:
        errors.append("blok bitis saati calismadi yazisi duruyor")
    lower = blob.lower()
    if "blok dışı" in blob or "blok disi" in lower:
        errors.append("blok disi ifadesi duruyor")
    if "09-11 arama blogu" in blob or "blok kart" in lower:
        errors.append("blok karti ifadesi duruyor")
    hour_cols = {
        "giden arama",
        "ulaşılan görüşme",
        "dönüş araması",
        "gelen arama",
        "ulaşma oranı",
        "görüşme süresi",
        "randevu",
        "katıldı",
    }
    found_hour_table = False
    for frame in at.dataframe:
        try:
            cols = {str(c) for c in frame.value.columns}
        except Exception:
            continue
        if hour_cols.issubset(cols):
            found_hour_table = True
            break
    if not found_hour_table:
        errors.append("saatlik tablo kolonlari yok")
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

    today = date.today()
    # AppTest tarih Istanbul'a bagli degil; takvim gunu yeter.
    sat = today - timedelta(days=(today.weekday() - 5) % 7)
    friday = sat - timedelta(days=1)

    expanders = [e.label for e in at_admin.expander]
    if "saat kırılımı" in expanders:
        errors.append(f"saat kirilimi hala expander: {expanders}")
    checks = [getattr(c, "label", "") for c in at_admin.checkbox]
    if any("saat kırılımı" in str(c) for c in checks):
        errors.append(f"saat kirilimi checkbox duruyor: {checks}")

    def _hour_frame(at: Any) -> Any | None:
        wanted = {
            "saat",
            "giden arama",
            "ulaşılan görüşme",
            "randevu",
            "katıldı",
        }
        for frame in at.dataframe:
            try:
                cols = {str(c) for c in frame.value.columns}
            except Exception:
                continue
            if wanted.issubset(cols):
                return frame.value
        return None

    if at_admin.date_input:
        at_admin.date_input[0].set_value(sat)
        at_admin.run()
        print(_dump(at_admin, f"cumartesi {sat.isoformat()}"))
        sat_blob = _all_text(at_admin)
        if "09-11 arama blogu" in sat_blob or "11-14 toplanti blogu" in sat_blob:
            errors.append("cumarteside hafta ici blok etiketi var")
        if "blok dışı" in sat_blob:
            errors.append("cumarteside blok disi var")
        sat_df = _hour_frame(at_admin)
        if sat_df is None:
            errors.append("cumartesi saatlik tablo yok")
        else:
            hours = [str(v) for v in sat_df["saat"].tolist()]
            print(f"cumartesi saat satirlar={hours}")
            if "09:00" not in hours or "14:00" not in hours:
                errors.append(f"cumartesi 09-15 araligi yok: {hours}")
            if "15:00" in hours or "17:00" in hours:
                errors.append(f"cumartesi fazla saat: {hours}")
            if "gün toplamı" not in hours:
                errors.append("cumartesi gun toplami satiri yok")
        if "doluluk 6 saat" not in sat_blob:
            errors.append("cumartesi doluluk 6 saat yazisi yok")
        print(f"cumartesi gun={sat.isoformat()}")

        at_admin.date_input[0].set_value(friday)
        at_admin.run()
        print(_dump(at_admin, f"cuma {friday.isoformat()}"))
        fri_blob = _all_text(at_admin)
        if "09-11 arama blogu" in fri_blob:
            errors.append("cumada blok karti duruyor")
        fri_df = _hour_frame(at_admin)
        if fri_df is None:
            errors.append("cuma saatlik tablo yok")
        else:
            hours = [str(v) for v in fri_df["saat"].tolist()]
            print(f"cuma saat satirlar={hours}")
            if "09:00" not in hours or "17:00" not in hours:
                errors.append(f"cuma 09-18 araligi yok: {hours}")
            if "gün toplamı" not in hours:
                errors.append("cuma gun toplami satiri yok")

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
        return 0
    time.sleep(2)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto("http://127.0.0.1:8501", wait_until="domcontentloaded")
            page.wait_for_selector('[data-testid="stApp"]', timeout=30000)
            page.wait_for_timeout(2000)
            inputs = page.locator('[data-testid="stTextInput"] input')
            inputs.nth(0).fill(ADMIN_EMAIL)
            inputs.nth(1).fill(VERIFY_PASSWORD)
            page.locator('[data-testid="stFormSubmitButton"] button').click()
            t_open = time.perf_counter()
            page.wait_for_selector("text=Ciro", timeout=120000)
            open_s = time.perf_counter() - t_open
            print(f"sayfa acilis (Ciro gorundu): {open_s:.2f}s")
            page.wait_for_timeout(3000)
            page.screenshot(
                path=str(SHOT_DIR / "yonetici.png"), full_page=True
            )
            login_left = page.locator('[data-testid="stFormSubmitButton"]')
            print(f"giris formu adet={login_left.count()}")
            page.screenshot(
                path=str(SHOT_DIR / "ingest-bar.png"),
                full_page=False,
            )
            hour_row = page.get_by_text("gün toplamı")
            print(f"gun toplami adet={hour_row.count()}")
            if hour_row.count() > 0:
                hour_row.first.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
            page.screenshot(
                path=str(SHOT_DIR / "saatlik-tablo.png"),
                full_page=True,
            )
            ciro = page.get_by_text("Ciro", exact=True)
            if ciro.count() > 0:
                ciro.first.scroll_into_view_if_needed()
                page.wait_for_timeout(1000)
                page.screenshot(
                    path=str(SHOT_DIR / "ciro.png"), full_page=True
                )
            sat_label = page.get_by_text("09:00-15:00")
            print(f"09:00-15:00 etiket adet={sat_label.count()}")
            weekday_blk = page.get_by_text("09-11 arama blogu")
            print(f"hafta ici 09-11 adet={weekday_blk.count()}")
            disi = page.get_by_text("blok dışı")
            print(f"blok disi adet={disi.count()}")
            doluluk6 = page.get_by_text("doluluk 6 saat")
            print(f"doluluk 6 saat adet={doluluk6.count()}")
            tabs = page.get_by_role("tab")
            print(f"playwright tab sayisi={tabs.count()}")
            browser.close()
    except Exception as exc:
        print(f"playwright hata: {exc}")
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
