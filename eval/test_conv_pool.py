"""Tutarlılık havuzu örneklemesi (saf)."""

from datetime import date

from pusula.conv_pool import sample

AUG, SEP = date(2026, 8, 1), date(2026, 9, 1)


def test_all_sales_and_proportional_stable_sample() -> None:
    rows = [{"id": i, "ay": AUG, "satis": i < 3} for i in range(30)]  # 3 satışlı, 27 satışsız
    rows += [{"id": 100 + i, "ay": SEP, "satis": False} for i in range(63)]
    picked = sample(rows, 30)
    sales = [r for r, pool, _ in picked if pool == "satis"]
    general = [(r, w) for r, pool, w in picked if pool == "genel"]
    assert len(sales) == 3
    per_month = {m: [w for r, w in general if r["ay"] == m] for m in (AUG, SEP)}
    assert (len(per_month[AUG]), len(per_month[SEP])) == (9, 21)  # 27/90, 63/90 orantılı
    assert per_month[AUG][0] == 3.0 and per_month[SEP][0] == 3.0
    assert sample(rows, 30) == picked  # kararlı
