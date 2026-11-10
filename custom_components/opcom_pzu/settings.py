"""Map config-entry options to the pricing model."""

from __future__ import annotations

from typing import Any, Mapping

from .const import (
    CONF_BUY_FIXED_CHARGES,
    CONF_BUY_FORMULA,
    CONF_BUY_MARKUP,
    CONF_BUY_PLAN,
    CONF_BUY_VAT,
    CONF_SELL_COMMISSION,
    CONF_SELL_FIXED_FEE,
    CONF_SELL_FORMULA,
    CONF_SELL_PLAN,
)
from .plans import CUSTOM
from .pricing import Tariff

DEFAULTS: dict[str, Any] = {
    CONF_SELL_COMMISSION: 0.0,
    CONF_SELL_FIXED_FEE: 0.0,
    CONF_SELL_FORMULA: "",
    CONF_BUY_MARKUP: 0.0,
    CONF_BUY_FIXED_CHARGES: 0.0,
    CONF_BUY_VAT: 21.0,
    CONF_BUY_FORMULA: "",
    CONF_BUY_PLAN: CUSTOM,
    CONF_SELL_PLAN: CUSTOM,
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

