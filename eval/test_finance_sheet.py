"""Haftalık bilanço tablosu (CSV): blok eşleme, otomatik satırların atlanması, şirket kuralı."""

from decimal import Decimal

from pusula.finance.sheet import amount, company_of, parse


def _csv(rows: list[tuple[list[str], list[str], list[str]]]) -> str:
    """Üç blok sütunu: B-F, H-L, N-R (A, G, M boş)."""
    out = []
    for left, mid, right in rows:
        cells = [""] + left + [""] + mid + [""] + right
        out.append(",".join(f'"{c}"' for c in cells))
    return "\n".join(out)


E = ["", "", "", "", ""]
SHEET = _csv(
    [
        (["Kalem", "Detay", "CCY", "Orijinal Tutar", "→ USD"], ["Kalem", "Detay", "CCY", "Orijinal Tutar", "→ USD"], E),
        (["FİNTECH HESAPLARIMIZ", "", "", "", ""], ["TEDARİKÇİ CÜZDAN BAKİYELERİ", "", "", "", ""], ["PLATFORM ALACAKLARI", "", "", "", ""]),
        (["WISE", "", "USD", "$1.000,00", "$1.000,00"], ["Aspol", "US", "USD", "$50,00", "$50,00"], ["Ali", "Trendyol", "TRY", "2.500,00 ₺", "$50,00"]),
        (["  Toplam — FİNTECH", "", "", "", "$1.000,00"], E, E),
        (["BANKA HESAPLARIMIZ", "", "", "", ""], ["AY BAZLI ÖDEME BEKLEYEN", "", "", "", ""], ["KİRALAMA GELİRLERİ", "", "", "", ""]),
        (["COMMERZBANK", "", "EUR", "100,00 €", "$110,00"], ["Maaşlar", "Cari Dönem", "USD", "$300,00", "$300,00"], ["Atölye Kira", "", "USD", "$20,00", "$20,00"]),
        (["KUVEYTTÜRK", "", "TRY", "9.999,00 ₺", "$200,00"], ["Ofis Kirası", "TR", "USD", "$10,00", "$10,00"], E),
        (["", "", "", "", "-"], ["Maaş Kalan", "LLC", "EUR", "40,00 €", "$45,00"], E),
        (["KREDİ KARTI BAKİYELERİ", "", "", "", ""], E, E),
        (["GARANTİ KREDİ KARTI", "Borç (-)", "TRY", "(1.913,80 ₺)", "($38,00)"], E, E),
        (["MERCURY KREDİ KARTI", "Borç (-)", "USD", "($213,00)", "($213,00)"], E, E),
        (["ÖDEME ALTYAPILARIMIZ", "", "", "", ""], E, E),
        (["İYZİCO", "bekleniyor", "TRY", "4.314,00 ₺", "$85,00"], E, E),
        (["STRIPE GMBH", "bekleniyor", "EUR", "8,50 €", "$9,90"], E, E),
        (["KASA", "", "TRY", "850,00 ₺", "$17,00"], E, E),
        (["AKTİFLER TOPLAM", "", "", "", "$9.999,00"], E, E),
    ]
)


def test_amount_formats() -> None:
    assert amount("$404.809,38") == Decimal("404809.38")
    assert amount("(191.380,00 ₺)") == Decimal("-191380.00")
    assert amount("855,22 €") == Decimal("855.22")
    assert amount("-") is None and amount("") is None


def test_company_rule() -> None:
    assert company_of("Ofis Kirası", "GMBH", "EUR") == "gmbh"
    assert company_of("Maaş Kalan", "LLC", "EUR") == "llc"
    assert company_of("Ofis Kirası", "TR", "USD") == "tr"
    assert company_of("STRIPE GMBH", "", "EUR") == "gmbh"
    assert company_of("FedEx", "", "TRY") == "tr"
    assert company_of("Leasing", "", "EUR") == "gmbh"
    assert company_of("Shipstation", "", "GBP") == "llc"


def test_parse_skips_automatic_rows_and_maps_groups() -> None:
    by_label = {r.label: r for r in parse(SHEET)}
    # Otomatik çekilenler tablodan alınmaz
    for auto in ("WISE", "Aspol", "KUVEYTTÜRK", "MERCURY KREDİ KARTI", "İYZİCO"):
        assert auto not in by_label
    assert by_label["COMMERZBANK"].group_name == "Hazır değerler" and by_label["COMMERZBANK"].company_id == "gmbh"
    card = by_label["GARANTİ KREDİ KARTI"]
    assert (card.section, card.group_name, card.amount, card.company_id) == ("current_liabilities", "Mali borçlar", Decimal("1913.80"), "tr")
    assert by_label["KASA"].group_name == "Hazır değerler"  # ödeme altyapısı bloğunda ama kasa
    assert by_label["STRIPE GMBH"].group_name == "Ticari alacaklar"
    assert by_label["Maaşlar · Cari Dönem"].group_name == "Ödenecek vergi, SGK ve maaş"
    assert by_label["Ofis Kirası"].group_name == "Diğer borçlar" and by_label["Ofis Kirası"].company_id == "tr"
    # Kira geliri alacak tarafında kalır (borçtaki "kira" kuralı uygulanmaz)
    rent = by_label["Atölye Kira"]
    assert (rent.section, rent.group_name, rent.amount) == ("current_assets", "Diğer alacaklar", Decimal("20.00"))
    assert by_label["Ali · Trendyol"].company_id == "tr"
    assert "AKTİFLER TOPLAM" not in by_label
