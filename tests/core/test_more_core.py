"""More unit tests: invariants of the planner, pricing edge cases, series, blocks."""

import random
from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.opcom_pzu import settings
from custom_components.opcom_pzu.api import OpcomParseError, day_bounds, parse_csv
from custom_components.opcom_pzu.arbitrage import BatteryParams, Slot, plan
from custom_components.opcom_pzu.pricing import Formula, FormulaError, Tariff
from custom_components.opcom_pzu.sensor import _blocks
from custom_components.opcom_pzu.series import Series, stats

from .test_core import load

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


def slots(prices, sell=None):
    sell = sell or prices
    return [Slot(T0 + STEP * i, b, s) for i, (b, s) in enumerate(zip(prices, sell))]


# -- planner invariants --------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_plan_invariants_on_random_days(seed):
    rnd = random.Random(seed)
    params = BatteryParams(
        capacity_kwh=rnd.choice([2, 5, 10, 40]),
        max_charge_kw=rnd.choice([0.3, 1.2, 5, 12]),
        max_discharge_kw=rnd.choice([0.3, 1.2, 5, 12]),
        round_trip_efficiency=rnd.choice([0.7, 0.9, 1.0]),
        degradation_per_kwh=rnd.choice([0, 0.1, 0.4]),
    )
    prices = [rnd.uniform(-0.2, 2.5) for _ in range(rnd.choice([12, 48, 96]))]
    initial = rnd.choice([0.0, params.capacity_kwh / 2])
    p = plan(slots(prices), 0.25, params, initial)

    assert p.net_gain >= 0  # doing nothing is always possible
    assert p.profitable == (p.net_gain > 0)
    charge = [s for s in p.slots if s.action == "charge"]
    discharge = [s for s in p.slots if s.action == "discharge"]
    # Power limits (grid side) hold in every slot.
    assert all(s.grid_kwh <= params.max_charge_kw * 0.25 + 1e-6 for s in charge)
    assert all(s.grid_kwh <= params.max_discharge_kw * 0.25 + 1e-6 for s in discharge)
    # Energy balance: what is sold comes from what was stored.
    one_way = params.round_trip_efficiency**0.5
    stored_in = sum(s.grid_kwh for s in charge) * one_way + initial
    # (the planner rounds the starting energy to its grain, at most capacity/20)
    assert sum(s.grid_kwh for s in discharge) / one_way <= stored_in + params.capacity_kwh / 20 + 1e-6
    # Replaying the schedule never leaves the battery below empty or above full.
    level = initial
    for s in p.slots:
        level += s.grid_kwh * one_way if s.action == "charge" else -s.grid_kwh / one_way
        assert -params.capacity_kwh / 20 - 1e-6 <= level <= params.capacity_kwh * 1.05 + 1e-6  # grain


def test_flat_prices_never_trade():
    assert not plan(slots([1.0] * 96), 0.25, BatteryParams()).profitable


def test_a_dearer_battery_never_gains_more():
    prices = [0.3] * 8 + [2.0] * 8 + [0.4] * 8 + [2.2] * 8
    gains = [plan(slots(prices), 0.25, BatteryParams(degradation_per_kwh=d)).net_gain for d in (0, 0.2, 0.6, 5)]
    assert gains == sorted(gains, reverse=True) and gains[-1] == 0


def test_better_efficiency_never_gains_less():
    prices = [0.3] * 8 + [1.5] * 8
    gains = [plan(slots(prices), 0.25, BatteryParams(round_trip_efficiency=e)).net_gain for e in (0.6, 0.8, 1.0)]
    assert gains == sorted(gains)


def test_negative_buy_price_pays_to_charge():
    p = plan(slots([-1.0, -1.0, 0.5, 0.5], [-1.0, -1.0, 0.5, 0.5]), 0.25, BatteryParams(degradation_per_kwh=0))
    assert p.profitable and p.slots[0].action == "charge"


def test_own_buy_and_sell_prices_are_used():
    # Cheap to buy, but the sell price is always below the break-even: no trade.
    assert not plan(slots([0.1] * 4 + [0.1] * 4, [0.05] * 8), 0.25, BatteryParams()).profitable


def test_empty_and_degenerate_inputs():
    assert not plan([], 0.25, BatteryParams()).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(capacity_kwh=0)).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(max_charge_kw=0)).profitable
    assert not plan(slots([1, 2]), 0.25, BatteryParams(max_discharge_kw=0)).profitable


def test_slot_with_no_time_left_does_nothing():
    cheap_then_dear = [Slot(T0, 0.1, 0.1, fraction=0.0), Slot(T0 + STEP, 3.0, 3.0)]
    assert not plan(cheap_then_dear, 0.25, BatteryParams()).profitable


def test_hourly_resolution():
    prices = [0.3, 0.3, 2.0, 2.0]
    p = plan([Slot(T0 + timedelta(hours=i), x, x) for i, x in enumerate(prices)], 1.0, BatteryParams())
    assert p.profitable and p.slots[0].start == T0


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
    b = settings.battery({})
    assert (b.capacity_kwh, b.max_charge_kw, b.max_discharge_kw) == (10.0, 5.0, 5.0)
    assert b.round_trip_efficiency == pytest.approx(0.9)
    assert settings.tariff({}).buy_vat_pct == 21.0
    over = settings.battery({"battery_charge_power_kw": 0.3, "battery_discharge_power_kw": 1.2, "battery_power_kw": 9})
    assert (over.max_charge_kw, over.max_discharge_kw) == (0.3, 1.2)  # the new fields win over the legacy one


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


# -- schedule blocks -----------------------------------------------------------


def test_blocks_merge_neighbouring_slots():
    p = plan(slots([0.2] * 4 + [3.0] * 4), 0.25, BatteryParams(degradation_per_kwh=0))
    blocks = _blocks(p, STEP)
    assert [b["action"] for b in blocks] == ["charge", "discharge"]
    assert blocks[0]["from"] != blocks[0]["to"]
    assert blocks[0]["avg_price"] == pytest.approx(0.2) and blocks[1]["avg_price"] == pytest.approx(3.0)
    assert blocks[0]["kwh"] == pytest.approx(sum(s.grid_kwh for s in p.slots if s.action == "charge"), abs=0.01)


def test_blocks_split_when_not_contiguous():
    p = plan(slots([0.2, 3.0, 3.0, 0.2, 3.0]), 0.25, BatteryParams(capacity_kwh=1.2, degradation_per_kwh=0))
    actions = [b["action"] for b in _blocks(p, STEP)]
    assert actions.count("charge") >= 1 and "discharge" in actions
