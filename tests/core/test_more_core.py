"""More unit tests: pricing edge cases, parser, series."""

from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.opcom_pzu import settings
from custom_components.opcom_pzu.api import OpcomParseError, day_bounds, parse_csv
from custom_components.opcom_pzu.pricing import Formula, FormulaError, Tariff
from custom_components.opcom_pzu.series import Series, stats

from .test_core import load

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


# -- pricing -------------------------------------------------------------------


@pytest.mark.parametrize(
    "buy, sell, spot, expect_buy, expect_sell",
    [
        ("(pzu + 0.10 + 0.464) * 1.21", "pzu", 0.5, 1.28744, 0.5),  # E.ON Dinamic Pro
        ("(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.464) * 1.21", "max(pzu, 0)", -0.2, 0.67912, 0.0),  # PPC
        ("(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.464) * 1.21", "max(pzu, 0)", 3.0, 1.8932, 3.0),
        ("(min(0.75*pzu + 0.1375, 0.55) + 0.464) * 1.21", "max(0.125 + 0.75*pzu, 0.5)", 0.4, 1.0908, 0.5),  # Yellowgrid
        ("(min(0.75*pzu + 0.1375, 0.55) + 0.464) * 1.21", "max(0.125 + 0.75*pzu, 0.5)", 2.0, 1.2269, 1.625),
    ],
)
def test_supplier_formulas(buy, sell, spot, expect_buy, expect_sell):
    t = Tariff(buy_formula=buy, sell_formula=sell).compile()
    assert t.buy_price(spot) == pytest.approx(expect_buy, abs=0.01)
    assert t.sell_price(spot) == pytest.approx(expect_sell, abs=0.01)


def test_simple_tariff_with_negative_price():
    t = Tariff(sell_commission_pct=10, sell_fixed_fee=0.05, buy_markup_pct=0, buy_fixed_charges=0.5, buy_vat_pct=21).compile()
    assert t.sell_price(-0.2) == pytest.approx(-0.2 * 0.9 - 0.05)
    assert t.buy_price(-0.2) == pytest.approx((-0.2 + 0.5) * 1.21)


def test_formula_uses_the_hour():
    f = Formula("pzu + hour/100")
    assert f(1.0, 12) == pytest.approx(1.12)


@pytest.mark.parametrize(
    "bad",
    ["pzu.real", "pzu[0]", "lambda: 1", "pzu if 1 else 2", "True", "'a'", "open('x')", "pzu <", "", "unknown_var * 2", "min(x=1)"],
)
def test_more_formulas_rejected(bad):
    with pytest.raises(FormulaError):
        Formula(bad)


def test_runtime_error_is_not_cached_as_a_value(caplog):
    t = Tariff(buy_formula="1/pzu", buy_vat_pct=0).compile()
    assert t.buy_price(0.0) == 0.0  # fell back to the simple fields
    assert t.buy_price(2.0) == 0.5  # and the formula still works afterwards


def test_describe_shows_what_is_in_use():
    d = Tariff(sell_formula="pzu*0.9").compile().describe()
    assert d["sell"] == "pzu*0.9" and "VAT" in d["buy"]


# -- settings ------------------------------------------------------------------


def test_settings_defaults_and_overrides():
    assert settings.tariff({}).buy_vat_pct == 21.0
    assert settings.tariff({"buy_vat_pct": 9.0, "sell_formula": "pzu"}).sell_formula == "pzu"


# -- parser --------------------------------------------------------------------


def test_parser_rejects_garbage():
    for text in ("", "<html>", '"Zona de tranzactionare","Interval"\n', "just,some,text\n1,2,3\n"):
        with pytest.raises(OpcomParseError):
            parse_csv(text, date(2026, 11, 9))


def test_english_file_in_eur():
    text = (
        '"PIP"\n\n"","Average price [EUR/MWh]"\n"ROPEX_DAM_Base (1-24)","1,052.30"\n\n'
        '"Trading zone","Interval","Closing price [EUR/MWh]","Volume"\n'
        + "".join(f'"Romania","{i}","{1000 + i:,}.50","10.0"\n' for i in range(1, 97))
    )
    d = parse_csv(text, date(2026, 11, 9))
    assert d.base == 1052.30 and d.intervals[0].price == 1001.5


def test_a_day_is_the_berlin_day():
    start, end = day_bounds(date(2026, 3, 29))  # spring forward
    assert end - start == timedelta(hours=23)


def test_bom_is_ignored():
    text = "﻿" + (load.__globals__["FIX"] / "ro_09-11-2026.csv").read_text(encoding="utf-8")
    assert len(parse_csv(text, date(2026, 11, 9)).intervals) == 96


# -- series --------------------------------------------------------------------


@pytest.fixture
def series():
    return Series([load("ro_09-11-2026.csv", date(2026, 11, 9))])


def test_series_at_and_following(series):
    first = series.points[0]
    mid = first.start + timedelta(minutes=7)
    assert series.at(mid) is first
    assert series.following(mid) is series.points[1]
    assert series.at(first.start - timedelta(seconds=1)) is None
    assert series.following(series.points[-1].start) is None  # nothing after the last interval
    assert series.at(series.points[-1].end) is None


def test_series_prices_are_per_kwh(series):
    d = load("ro_09-11-2026.csv", date(2026, 11, 9))
    assert series.points[0].pzu == pytest.approx(d.intervals[0].price / 1000)


def test_series_covers_only_complete_windows(series):
    s, e = series.points[0].start, series.points[-1].end
    assert len(series.covers(s, e)) == 96
    assert series.covers(s - STEP, e) is None
    assert series.covers(s, e + STEP) is None
    assert series.covers(s, s) is None


def test_series_across_days_is_sorted():
    a = load("ro_09-11-2026.csv", date(2026, 11, 9))
    b = load("ro_25-10-2026.csv", date(2026, 10, 25))
    s = Series([a, b])
    starts = [p.start for p in s.points]
    assert starts == sorted(starts) and len(starts) == 196


def test_stats_are_time_weighted():
    from custom_components.opcom_pzu.series import Point

    pts = [Point(T0, T0 + timedelta(hours=1), 1.0), Point(T0 + timedelta(hours=1), T0 + timedelta(hours=4), 3.0)]
    s = stats(pts)
    assert s.average == pytest.approx((1 * 1 + 3 * 3) / 4)
    assert (s.minimum, s.maximum) == (1.0, 3.0) and s.max_start == pts[1].start
    assert stats([]) is None
    assert stats(pts, [10.0, -5.0]).minimum == -5.0  # custom prices (buy/sell) override spot
