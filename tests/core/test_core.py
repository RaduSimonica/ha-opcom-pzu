"""Tests for the parser, pricing formulas and arbitrage planner."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.opcom_pzu.api import OpcomParseError, parse_csv
from custom_components.opcom_pzu.pricing import Formula, FormulaError, Tariff

FIX = Path(__file__).parent.parent / "fixtures"


def load(name: str, day: date):
    return parse_csv((FIX / name).read_text(encoding="utf-8"), day)


def test_normal_day():
    d = load("ro_09-11-2026.csv", date(2026, 11, 9))
    assert len(d.intervals) == 96 and d.step == timedelta(minutes=15)
    # Interval 1 starts 00:00 CET = 23:00 UTC the day before.
    assert d.intervals[0].start == datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)
    assert d.base == 2073.46 and d.peak == 2407.63 and d.off_peak == 1739.29


def test_dst_days():
    autumn = load("ro_25-10-2026.csv", date(2026, 10, 25))
    spring = load("ro_29-03-2026.csv", date(2026, 3, 29))
    assert len(autumn.intervals) == 100 and len(spring.intervals) == 92
    for d in (autumn, spring):
        starts = [i.start for i in d.intervals]
        assert all(b - a == timedelta(minutes=15) for a, b in zip(starts, starts[1:]))


def test_hourly_legacy_file():
    text = '"PIP"\n\n"","Pret mediu [lei/MWh]","Volum [MWh]"\n\n"Zona de tranzactionare","Interval","Pret","Vol"\n' + "".join(
        f'"Romania","{i}","{100 + i}.50","10.0","1","1"\n' for i in range(1, 25)
    )
    d = parse_csv(text, date(2024, 6, 1))
    assert len(d.intervals) == 24 and d.step == timedelta(hours=1)


def test_bad_csv():
    with pytest.raises(OpcomParseError):
        parse_csv('"hello"\n', date(2026, 1, 1))
    with pytest.raises(OpcomParseError):  # 50 rows fit no known resolution
        parse_csv('"Zona de tranzactionare"\n' + '"R","1","1","1"\n' * 50, date(2026, 1, 1))


def test_simple_tariff():
    t = Tariff(sell_commission_pct=10, sell_fixed_fee=0.01, buy_fixed_charges=0.4, buy_vat_pct=21).compile()
    assert t.sell_price(1.0) == pytest.approx(0.89)
    assert t.buy_price(1.0) == pytest.approx(1.4 * 1.21)


def test_advanced_formula():
    f = Formula("max(pzu*0.9 - 0.05, 0.1)")
    assert f(1.0, 0) == pytest.approx(0.85) and f(0.0, 0) == 0.1
    t = Tariff(buy_formula="(pzu + 0.3) * 1.21").compile()
    assert t.buy_price(1.0) == pytest.approx(1.3 * 1.21)


@pytest.mark.parametrize(
    "bad", ["__import__('os')", "pzu.real", "open('x')", "pzu +", "foo*2", "[1]", "1 if pzu else 2"]
)
def test_formula_rejects(bad):
    with pytest.raises(FormulaError):
        Formula(bad)


# -- regressions found in the QA pass ----------------------------------------


def test_formula_without_an_answer_falls_back_to_simple_fields(caplog):
    # Negative and zero prices exist; some formulas have no answer for them.
    tariff = Tariff(buy_formula="1/pzu", sell_formula="pzu**0.5", sell_commission_pct=10).compile()
    assert tariff.buy_price(0.0) == pytest.approx(0.0 * 1.21)  # falls back to (pzu*..)*(1+VAT=0)
    assert tariff.sell_price(-0.1) == pytest.approx(-0.09)  # complex result -> simple fields
    assert tariff.sell_price(0.25) == pytest.approx(0.5)  # normal prices still use the formula
    assert caplog.text.count("cannot be computed") == 2  # warned once per formula, not per price


def test_footer_after_the_table_is_ignored():
    text = (FIX / "ro_09-11-2026.csv").read_text(encoding="utf-8") + '\n"Note","footer text"\n'
    assert len(parse_csv(text, date(2026, 11, 9)).intervals) == 96


def test_a_missing_row_still_fails():
    lines = (FIX / "ro_09-11-2026.csv").read_text(encoding="utf-8").splitlines()
    cut = [ln for i, ln in enumerate(lines) if i != len(lines) - 5]
    with pytest.raises(OpcomParseError):
        parse_csv("\n".join(cut), date(2026, 11, 9))


def test_number_formats():
    from custom_components.opcom_pzu.api import _num

    assert _num("1234.5") == _num("1234,5") == _num("1,234.5") == 1234.5


def test_window_includes_an_interval_in_progress():
    from custom_components.opcom_pzu.series import Series

    d = load("ro_09-11-2026.csv", date(2026, 11, 9))
    s = Series([d])
    start = d.intervals[10].start + timedelta(minutes=5)
    assert s.window(start, start + timedelta(minutes=20))[0].start == d.intervals[10].start
