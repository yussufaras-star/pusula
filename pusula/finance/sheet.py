"""Haftalık elle güncellenen bilanço tablosu (Google Sheets, CSV dışa aktarımı).

Tablo üç blok sütunu yan yana tutar (aktif | pasif | vadeli alacak); her blok
bir başlık satırı ve altında "Kalem, Detay, CCY, Orijinal Tutar" satırlarıdır.
Anlık bilanço yalnız otomatik çekilemeyen kalemleri buradan alır: Pusula'nın
API / Rexven ile çektikleri (Wise, Mercury, Kuveyt, iyzico, Lidio, Stripe LLC,
cüzdanlar) atlanır, iki kez sayılmasın.

Ortam değişkeni: FIN_BALANCE_SHEET_CSV (tablonun CSV dışa aktarım adresi; depo
herkese açık olduğu için kodda tutulmaz).
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx

from pusula.finance.providers.base import HTTP_TIMEOUT

ENV = "FIN_BALANCE_SHEET_CSV"
CURRENCIES = frozenset({"USD", "TRY", "EUR", "GBP"})
# Blok sütunları: (kalem sütunu, tarafı) — Detay, CCY, Tutar hemen sağında
COLUMNS = ((1, "asset"), (7, "liability"), (13, "asset"))

# Blok başlığı → (bölüm, grup); None ise blok atlanır (otomatik geliyor)
BLOCKS: dict[str, tuple[str, str] | None] = {
    "FİNTECH HESAPLARIMIZ": None,
    "BANKA HESAPLARIMIZ": ("current_assets", "Hazır değerler"),
    "KREDİ KARTI BAKİYELERİ": ("current_liabilities", "Mali borçlar"),
    "ÖDEME ALTYAPILARIMIZ": ("current_assets", "Ticari alacaklar"),
    "ALACAK BAKİYELERİ": ("current_assets", "Verilen avanslar ve peşin giderler"),
    "TEDARİKÇİ CÜZDAN BAKİYELERİ": None,
    "SATICI CÜZDAN (MÜŞTERİ AVANSLARI)": None,
    "KARGO-ARA DEPO (BORÇ)": ("current_liabilities", "Ticari borçlar"),
    "AY BAZLI ÖDEME BEKLEYEN": ("current_liabilities", "Ödenecek vergi, SGK ve maaş"),
    "PLATFORM ALACAKLARI": ("current_assets", "Ticari alacaklar"),
    "PROJE GELİRLERİ": ("current_assets", "Ticari alacaklar"),
    "STOK GELİRLERİ": ("current_assets", "Diğer alacaklar"),
    "KİRALAMA GELİRLERİ": ("current_assets", "Diğer alacaklar"),
}
# Bilinmeyen yeni blok: tarafının "diğer" grubuna
OTHER = {
    "asset": ("current_assets", "Diğer dönen varlıklar"),
    "liability": ("current_liabilities", "Diğer kısa vadeli yabancı kaynaklar"),
}
# Otomatik çekilen satırlar (blok içinde kalem adıyla)
AUTO_ROWS = frozenset({"KUVEYTTÜRK", "MERCURY KREDİ KARTI", "İYZİCO", "LİDİO", "STRIPE LLC"})
# Kalem adına göre grup düzeltmesi: anahtar kalem adında geçerse (yalnız aynı taraftaki blokta)
ROW_GROUP = (
    ("KASA", ("current_assets", "Hazır değerler")),
    ("KİRA", ("current_liabilities", "Diğer borçlar")),
    ("SMM", ("current_liabilities", "Diğer borçlar")),
)
# Detay sütununda şirketi değil durumu anlatan kısa notlar etikete eklenmez
QUIET_DETAILS = frozenset({"", "TR", "US", "DEU", "LLC", "GMBH", "BORÇ (-)", "BEKLENIYOR", "BEKLENİYOR"})


@dataclass(frozen=True)
class SheetLine:
    company_id: str
    section: str
    group_name: str
    label: str
    amount: Decimal  # bölümün yönünde pozitif (kredi kartı borcu pozitif borç)
    currency: str


def norm(text: str) -> str:
    return " ".join(text.replace("i", "İ").replace("ı", "I").upper().split()) if text else ""


def amount(text: str) -> Decimal | None:
    """'$404.809,38', '(191.380,00 ₺)', '855,22 €' → Decimal; boş / '-' → None."""
    raw = (text or "").strip()
    negative = raw.startswith("(") and raw.endswith(")") or raw.startswith("-")
    digits = "".join(ch for ch in raw if ch.isdigit() or ch in ",.")
    if not digits:
        return None
    try:
        value = Decimal(digits.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None
    return -value if negative else value


def side_of(section: str) -> str:
    return "asset" if section.endswith("assets") else "liability"


def company_of(label: str, detail: str, currency: str) -> str:
    """Detayda / kalemde şirket yazıyorsa o; yoksa para birimi: TRY → AŞ, EUR → GmbH, diğer → LLC."""
    text = f"{norm(label)} {norm(detail)}"
    if "GMBH" in text:
        return "gmbh"
    if "LLC" in text.split():
        return "llc"
    if norm(detail) == "TR":
        return "tr"
    return {"TRY": "tr", "EUR": "gmbh"}.get(currency, "llc")


def parse(text: str) -> list[SheetLine]:
    rows = list(csv.reader(io.StringIO(text)))
    out: list[SheetLine] = []
    for col, side in COLUMNS:
        block: tuple[str, str] | None = None  # ilk başlıktan önce satır alınmaz
        for row in rows:
            cells = [(row[i] if i < len(row) else "").strip() for i in range(col, col + 5)]
            label, detail, ccy, value, usd = cells
            if not label or norm(label).startswith("TOPLAM"):
                continue
            if not (detail or ccy or value or usd):
                block = BLOCKS.get(norm(label), OTHER[side])
                continue
            num = amount(value)
            if block is None or ccy not in CURRENCIES or not num or norm(label) in AUTO_ROWS:
                continue
            section, group = next((g for k, g in ROW_GROUP if k in norm(label) and side_of(g[0]) == side_of(block[0])), block)
            if section.endswith("liabilities") and side == "asset":
                num = -num  # aktif blokta eksi yazılmış borç (kredi kartı)
            shown = label if norm(detail) in QUIET_DETAILS else f"{label} · {detail}"
            out.append(SheetLine(company_of(label, detail, ccy), section, group, shown, num, ccy))
    return out


def load() -> tuple[list[SheetLine], str | None]:
    """(satırlar, hata). Adres tanımlı değilse boş liste ve hata yok."""
    url = os.environ.get(ENV, "").strip()
    if not url:
        return [], None
    try:
        resp = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return parse(resp.text), None
    except (httpx.HTTPError, csv.Error) as exc:
        return [], f"Bilanço tablosu okunamadı ({type(exc).__name__})"
