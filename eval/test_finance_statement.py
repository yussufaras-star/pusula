"""Ekstre ayrıştırma: sayı/tarih, başlık bulma, borç/alacak, tekrar yüklemede aynı kimlik."""

import io
from datetime import date, datetime
from decimal import Decimal

import pytest

from pusula.finance.statement import (
    StatementError,
    StatementMapping,
    apply_mapping,
    bank_counterparty,
    find_header,
    guess_mapping,
    parse_date,
    parse_number,
    read_rows,
)

D = Decimal

GARANTI_CSV = """Garanti BBVA Hesap Hareketleri
Hesap No;TR12 0006 2000 0000 0000 0000 01
Müşteri;REXVEN TEKNOLOJİ A.Ş.

Tarih;Açıklama;Etiket;Tutar;Bakiye;Dekont No
01.10.2026;Devreden bakiye;;0,00;10.000,00;
02.10.2026;EFT GELEN REXVEN LLC;;250.000,00;260.000,00;111
03.10.2026;KARGO ÖDEMESİ;;-1.234,56;258.765,44;112
03.10.2026;POS komisyon;;-12,50;258.752,94;113
03.10.2026;POS komisyon;;-12,50;258.740,44;114
"""

# Borç / alacak ayrı sütun, yeniden eskiye sıralı, cp1254.
KUVEYT_CSV = (
    "İşlem Tarihi;Açıklama;Borç;Alacak;Bakiye\n"
    "05.10.2026;Kira;30.000,00;;70.000,00\n"
    "05.10.2026;Tahsilat;;5.000,00;100.000,00\n"
    "04.10.2026;Maaş ödemesi;45.000,00;;95.000,00\n"
)


def _parse(text: str, encoding: str = "utf-8"):
    sheet = find_header(read_rows("ekstre.csv", text.encode(encoding)))
    mapping = guess_mapping(sheet.header)
    assert mapping is not None
    return sheet, mapping, apply_mapping(sheet, mapping)


# --- sayı ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", D("1234.56")),
        ("1,234.56", D("1234.56")),
        ("(45,10)", D("-45.10")),
        ("45,10-", D("-45.10")),
        ("-1234.5", D("-1234.5")),
        ("1.234.567", D("1234567")),
        ("1.234", D("1234")),
        ("12,5", D("12.5")),
        ("₺ 2.500,00 TL", D("2500.00")),
        ("+300", D("300")),
        (1234.56, D("1234.56")),
        (7, D("7")),
        ("", None),
        ("-", None),
        (None, None),
        (date(2026, 1, 1), None),
    ],
)
def test_parse_number(raw, expected) -> None:
    assert parse_number(raw) == expected


# --- tarih -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("15.10.2026", date(2026, 10, 15)),
        ("5.1.2026", date(2026, 1, 5)),
        ("15/10/2026", date(2026, 10, 15)),
        ("15-10-26", date(2026, 10, 15)),
        ("15.10.2026 14:22:01", date(2026, 10, 15)),
        ("2026-10-15", date(2026, 10, 15)),
        (46310, date(2026, 10, 15)),  # Excel seri tarih
        (46310.6, date(2026, 10, 15)),
        ("46310", date(2026, 10, 15)),
        (datetime(2026, 10, 15, 9, 30), date(2026, 10, 15)),
        (1234, None),  # tutar, tarih değil
        ("31.02.2026", None),
        ("Devreden", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected) -> None:
    assert parse_date(raw) == expected


# --- başlık ve eşleme ------------------------------------------------------------


def test_header_found_after_info_rows() -> None:
    sheet, mapping, _ = _parse(GARANTI_CSV)
    assert sheet.header_index == 4
    assert sheet.header[:5] == ["Tarih", "Açıklama", "Etiket", "Tutar", "Bakiye"]
    assert mapping == StatementMapping(date=0, description=1, amount=3, balance=4)


def test_single_amount_column_parses_and_skips_zero_rows() -> None:
    _, _, parsed = _parse(GARANTI_CSV)
    assert parsed.skipped == 1  # devreden bakiye
    assert [(t.booked_at.day, t.amount) for t in parsed.txs] == [
        (2, D("250000.00")),
        (3, D("-1234.56")),
        (3, D("-12.50")),
        (3, D("-12.50")),
    ]
    assert parsed.last_balance == D("258740.44")
    assert parsed.date_range == (date(2026, 10, 2), date(2026, 10, 3))


def test_split_debit_credit_columns_and_cp1254() -> None:
    sheet, mapping, parsed = _parse(KUVEYT_CSV, "cp1254")
    assert sheet.header[0] == "İşlem Tarihi"
    assert mapping.amount is None and mapping.debit == 2 and mapping.credit == 3
    assert [(t.description, t.amount) for t in parsed.txs] == [
        ("Kira", D("-30000.00")),
        ("Tahsilat", D("5000.00")),
        ("Maaş ödemesi", D("-45000.00")),
    ]
    # yeniden eskiye: aynı gündeki en üst satır son işlemdir
    assert parsed.last_balance == D("70000.00")


def test_debit_amount_column_not_taken_as_amount() -> None:
    mapping = guess_mapping(["Tarih", "Açıklama", "Borç Tutarı", "Alacak Tutarı", "Bakiye"])
    assert mapping == StatementMapping(date=0, description=1, debit=2, credit=3, balance=4)


def test_ascii_and_uppercase_headers() -> None:
    mapping = guess_mapping(["ISLEM TARIHI", "ACIKLAMA", "TUTAR"])
    assert mapping == StatementMapping(date=0, description=1, amount=2)
    assert guess_mapping(["Booking date", "Description", "Amount", "Balance"]) == StatementMapping(
        date=0, description=1, amount=2, balance=3
    )


def test_no_date_column_returns_none_for_user_choice() -> None:
    assert guess_mapping(["Açıklama", "Tutar"]) is None


def test_invert_flips_signs() -> None:
    sheet = find_header(read_rows("x.csv", GARANTI_CSV.encode()))
    mapping = guess_mapping(sheet.header)
    parsed = apply_mapping(sheet, mapping, invert=True)
    assert parsed.txs[0].amount == D("-250000.00")


# --- external_id -----------------------------------------------------------------


def test_identical_rows_get_distinct_ids() -> None:
    _, _, parsed = _parse(GARANTI_CSV)
    commission = [t for t in parsed.txs if t.description == "POS komisyon"]
    assert len(commission) == 2
    assert commission[0].external_id != commission[1].external_id
    assert len({t.external_id for t in parsed.txs}) == len(parsed.txs)


def test_reupload_produces_same_ids() -> None:
    first = _parse(GARANTI_CSV)[2]
    again = _parse(GARANTI_CSV)[2]
    assert [t.external_id for t in first.txs] == [t.external_id for t in again.txs]


def test_same_rows_in_overlapping_export_keep_ids() -> None:
    # Sonraki indirme farklı bilgi satırları ve ek bir gün içerse de kimlikler aynı
    later = GARANTI_CSV.replace("Garanti BBVA Hesap Hareketleri", "Rapor tarihi 06.10.2026") + (
        "04.10.2026;Yeni hareket;;-10,00;258.730,44;115\n"
    )
    first = {t.external_id for t in _parse(GARANTI_CSV)[2].txs}
    second = {t.external_id for t in _parse(later)[2].txs}
    assert first < second and len(second - first) == 1


def test_id_ignores_case_and_spacing_in_description() -> None:
    a = _parse("Tarih;Açıklama;Tutar\n01.10.2026;EFT  GELEN;10,00\n")[2].txs[0]
    b = _parse("Tarih;Açıklama;Tutar\n01.10.2026;eft gelen;10,00\n")[2].txs[0]
    assert a.external_id == b.external_id


# --- dosya biçimleri -------------------------------------------------------------


def test_comma_csv_with_quoted_decimals() -> None:
    text = 'Date,Description,Amount\n2026-10-01,"Stripe payout",1234.56\n2026-10-02,Fee,"-1,234.00"\n'
    _, _, parsed = _parse(text)
    assert [t.amount for t in parsed.txs] == [D("1234.56"), D("-1234.00")]


def test_xlsx_with_serial_and_datetime_cells() -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.append(["VakıfBank Hesap Ekstresi"])
    sheet.append([])
    sheet.append(["Tarih", "Açıklama", "Tutar", "Bakiye"])
    sheet.append([datetime(2026, 10, 1, 10, 0), "Havale", 1500.5, 2500.5])
    sheet.append([46297, "Fatura", -200, 2300.5])
    buffer = io.BytesIO()
    book.save(buffer)

    parsed_sheet = find_header(read_rows("ekstre.xlsx", buffer.getvalue()))
    mapping = guess_mapping(parsed_sheet.header)
    parsed = apply_mapping(parsed_sheet, mapping)
    assert [(t.booked_at, t.amount) for t in parsed.txs] == [
        (date(2026, 10, 1), D("1500.50")),
        (date(2026, 10, 2), D("-200.00")),
    ]
    assert parsed.last_balance == D("2300.50")


def test_html_table_saved_as_xls() -> None:
    html = (
        "<html><body><table>"
        "<tr><td>Hesap</td><td>TR12</td></tr>"
        "<tr><th>Tarih</th><th>Açıklama</th><th>Tutar</th></tr>"
        "<tr><td>01.10.2026</td><td>EFT <b>GELEN</b></td><td>1.000,00</td></tr>"
        "</table></body></html>"
    )
    rows = read_rows("ekstre.xls", html.encode("utf-8"))
    sheet = find_header(rows)
    parsed = apply_mapping(sheet, guess_mapping(sheet.header))
    assert [(t.description, t.amount) for t in parsed.txs] == [("EFT GELEN", D("1000.00"))]


def test_binary_xls_and_headerless_files_are_rejected() -> None:
    with pytest.raises(StatementError, match="XLSX veya CSV"):
        read_rows("eski.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
    with pytest.raises(StatementError, match="Başlık"):
        find_header(read_rows("x.csv", b"a;b\n1;2\n"))
    with pytest.raises(StatementError, match="boş"):
        read_rows("x.csv", b"")


@pytest.mark.parametrize(
    ("description", "amount", "expected"),
    [
        # KuveytTürk giden: alıcı
        ("ZFA2026000000006 - Gönderen:REXVEN TEKNOLOJİ ANONİM ŞİRKETİ, Alıcı:ZANAART TAKI ÜRETIM ANONIM ŞIRKETI,"
         " Para Transferi", "-241543.03", "ZANAART TAKI ÜRETIM ANONIM ŞIRKETI"),
        # gelen: gönderen; 'Para Transferi, Gönderen: X' biçimi
        ("REX-1 - Para Transferi, Gönderen: MEHMET GÖK, Alıcı:Rexven Teknoloji", "500", "MEHMET GÖK"),
        # EFT: 'Gönderen= X , Alıcı= Y', açıklama bitişik
        (", Gelen Para Transferi, Açıklama= REXVENGönderen= LİDİO ÖDEME HİZMETLERİ ANONİM ŞİRKETİ ,"
         " Alıcı= REXVEN TEKNOLOJİ ANONİM ŞİRKETİ", "7442.44", "LİDİO ÖDEME HİZMETLERİ ANONİM ŞİRKETİ"),
        # yurt dışından gelen: emir veren, sondaki referans numaraları atılır
        ("Amir:Neshe LLC 232690923 2026052200117758", "35000", "Neshe LLC"),
        ("Maaş Ödemesi", "-1000", None),
        ("Kredi Kartı No:5116********7269 , Kredi Kartı Borç Ödemesi", "-5", None),
    ],
)
def test_bank_counterparty(description: str, amount: str, expected: str | None) -> None:
    assert bank_counterparty(description, D(amount)) == expected
