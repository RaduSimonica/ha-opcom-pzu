"""Tests for the parser, pricing formulas and arbitrage planner."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from custom_components.opcom_pzu.api import OpcomParseError, parse_csv
from custom_components.opcom_pzu.arbitrage import BatteryParams, Slot, plan
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


def _slots(prices):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Slot(t0 + timedelta(minutes=15 * i), p, p) for i, p in enumerate(prices)]


def test_plan_profitable_and_ordering():
    prices = [0.3] * 8 + [1.5] * 8
    p = plan(_slots(prices), 0.25, BatteryParams(10, 10, 0.9, 0.1))
    assert p.profitable
    charges = [s.start for s in p.slots if s.action == "charge"]
    discharges = [s.start for s in p.slots if s.action == "discharge"]
    assert max(charges) < min(discharges)
    assert p.energy_sold_kwh <= 10  # never more than usable capacity
    assert p.gain_per_kwh_sold == pytest.approx(p.net_gain / p.energy_sold_kwh)
    # 1 kWh sold costs 0.3/0.9 + 0.1 degradation, so net per kWh ~ 1.067
    assert p.gain_per_kwh_sold == pytest.approx(1.5 - 0.1 - 0.3 / 0.9, abs=0.02)


def test_plan_not_profitable_when_spread_too_small():
    p = plan(_slots([0.50] * 8 + [0.52] * 8), 0.25, BatteryParams(10, 10, 0.9, 0.1))
    assert not p.profitable and p.net_gain == 0 and p.slots == []


def test_plan_respects_power_limit():
    p = plan(_slots([0.1] * 4 + [2.0] * 4), 0.25, BatteryParams(100, 4, 1.0, 0))
    assert p.energy_sold_kwh <= 4 * 0.25 * 4 + 1e-9  # 4 kW for 4 slots of 15 min


def test_plan_multiple_cycles():
    prices = [0.2] * 4 + [1.5] * 4 + [0.2] * 4 + [1.5] * 4
    p = plan(_slots(prices), 0.25, BatteryParams(2, 8, 1.0, 0))
    assert p.cycles == pytest.approx(2, abs=0.1)


def test_plan_sells_energy_already_in_battery():
    # Buying is never worth it (3.0 in), but 5 kWh are stored and the evening pays 2.0.
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sell = [0.5] * 8 + [2.0] * 4
    slots = [Slot(t0 + timedelta(minutes=15 * i), 3.0, p) for i, p in enumerate(sell)]
    p = plan(slots, 0.25, BatteryParams(10, 10, 0.9, 0.1), initial_kwh=5)
    assert p.profitable and p.energy_bought_kwh == 0
    assert 4 < p.energy_sold_kwh <= 5  # most of the stored energy goes out, none is invented
    assert p.gain_per_kwh_bought is None and p.break_even_sell_price is None
    assert p.gain_per_kwh_sold == pytest.approx(2.0 - 0.1, abs=0.02)  # no purchase cost


def test_plan_full_battery_cannot_charge_more():
    p = plan(_slots([0.1] * 4 + [2.0] * 4), 0.25, BatteryParams(4, 20, 1.0, 0), initial_kwh=4)
    assert p.energy_bought_kwh == 0 and p.energy_sold_kwh == pytest.approx(4, abs=0.3)


def test_plan_initial_energy_never_hurts():
    prices = [0.3] * 6 + [1.4] * 6
    base = plan(_slots(prices), 0.25, BatteryParams(10, 10, 0.9, 0.1))
    more = plan(_slots(prices), 0.25, BatteryParams(10, 10, 0.9, 0.1), initial_kwh=3)
    assert more.net_gain >= base.net_gain - 1e-9
