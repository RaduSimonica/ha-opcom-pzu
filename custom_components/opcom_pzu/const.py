"""Constants for the OPCOM PZU integration."""

from datetime import time

DOMAIN = "opcom_pzu"
NAME = "OPCOM PZU"

CONF_CURRENCY = "currency"
CONF_HISTORY_MONTHS = "history_months"

# Tariff (options)
CONF_SELL_COMMISSION = "sell_commission_pct"
CONF_SELL_FIXED_FEE = "sell_fixed_fee"
CONF_SELL_FORMULA = "sell_formula"
CONF_BUY_MARKUP = "buy_markup_pct"
CONF_BUY_FIXED_CHARGES = "buy_fixed_charges"
CONF_BUY_VAT = "buy_vat_pct"
CONF_BUY_FORMULA = "buy_formula"

# Battery (options)
CONF_BATTERY_CAPACITY = "battery_capacity_kwh"
CONF_BATTERY_POWER = "battery_power_kw"
CONF_BATTERY_EFFICIENCY = "battery_round_trip_efficiency_pct"
CONF_BATTERY_DEGRADATION = "battery_degradation_per_kwh"
CONF_BATTERY_SOC_ENTITY = "battery_soc_entity"  # optional: live state of charge
CONF_BATTERY_CAPACITY_ENTITY = "battery_capacity_entity"  # optional: live usable capacity

CONF_CHEAP_PERCENTILE = "cheap_percentile"

CURRENCIES = {"RON": "ro", "EUR": "en"}  # currency -> OPCOM language
DEFAULT_CURRENCY = "RON"
DEFAULT_HISTORY_MONTHS = 12
MAX_HISTORY_MONTHS = 132  # OPCOM data starts in 2015
DEFAULT_CHEAP_PERCENTILE = 25

# Tomorrow's prices: first try, then retry every RETRY_MINUTES (local time).
FIRST_FETCH = time(13, 15)
RETRY_MINUTES = 15
JITTER_SECONDS = 180

BACKFILL_DELAY = 1.0  # seconds between historic requests: be gentle to OPCOM
STORAGE_VERSION = 1
