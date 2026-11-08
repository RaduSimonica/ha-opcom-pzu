"""Map config-entry options to the pricing and battery models."""

from __future__ import annotations

from typing import Any, Mapping

from .arbitrage import BatteryParams
from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_CHARGE_POWER,
    CONF_BATTERY_DEGRADATION,
    CONF_BATTERY_DISCHARGE_POWER,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_POWER,
    CONF_BUY_FIXED_CHARGES,
    CONF_BUY_FORMULA,
    CONF_BUY_MARKUP,
    CONF_BUY_VAT,
    CONF_SELL_COMMISSION,
    CONF_SELL_FIXED_FEE,
    CONF_SELL_FORMULA,
)
from .pricing import Tariff

DEFAULTS: dict[str, Any] = {
    CONF_SELL_COMMISSION: 0.0,
    CONF_SELL_FIXED_FEE: 0.0,
    CONF_SELL_FORMULA: "",
    CONF_BUY_MARKUP: 0.0,
    CONF_BUY_FIXED_CHARGES: 0.0,
    CONF_BUY_VAT: 21.0,
    CONF_BUY_FORMULA: "",
    CONF_BATTERY_CAPACITY: 10.0,
    CONF_BATTERY_CHARGE_POWER: 5.0,
    CONF_BATTERY_DISCHARGE_POWER: 5.0,
    CONF_BATTERY_EFFICIENCY: 90.0,
    CONF_BATTERY_DEGRADATION: 0.15,
}


def get(options: Mapping[str, Any], key: str) -> Any:
    return options.get(key, DEFAULTS[key])


def tariff(options: Mapping[str, Any]) -> Tariff:
    return Tariff(
        sell_commission_pct=get(options, CONF_SELL_COMMISSION),
        sell_fixed_fee=get(options, CONF_SELL_FIXED_FEE),
        sell_formula=get(options, CONF_SELL_FORMULA),
        buy_markup_pct=get(options, CONF_BUY_MARKUP),
        buy_fixed_charges=get(options, CONF_BUY_FIXED_CHARGES),
        buy_vat_pct=get(options, CONF_BUY_VAT),
        buy_formula=get(options, CONF_BUY_FORMULA),
    )


def battery(options: Mapping[str, Any]) -> BatteryParams:
    # A single "power" limit was used before charge and discharge were split.
    legacy = options.get(CONF_BATTERY_POWER)
    return BatteryParams(
        capacity_kwh=get(options, CONF_BATTERY_CAPACITY),
        max_charge_kw=options.get(CONF_BATTERY_CHARGE_POWER, legacy or DEFAULTS[CONF_BATTERY_CHARGE_POWER]),
        max_discharge_kw=options.get(
            CONF_BATTERY_DISCHARGE_POWER, legacy or DEFAULTS[CONF_BATTERY_DISCHARGE_POWER]
        ),
        round_trip_efficiency=get(options, CONF_BATTERY_EFFICIENCY) / 100,
        degradation_per_kwh=get(options, CONF_BATTERY_DEGRADATION),
    )
