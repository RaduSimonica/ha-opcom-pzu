"""OPCOM PZU: Romanian day-ahead electricity prices, history and battery arbitrage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import OpcomClient
from .const import (
    CONF_CURRENCY,
    CONF_HISTORY_MONTHS,
    CURRENCIES,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    MAX_HISTORY_MONTHS,
)
from .coordinator import PzuCoordinator
from .history import Backfill
from .series import Series, local_day_bounds
from .store import PriceStore

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
MAX_RANGE_DAYS = 366

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class RuntimeData:
    coordinator: PzuCoordinator
    backfill: Backfill
    store: PriceStore


type PzuConfigEntry = ConfigEntry[RuntimeData]


def history_months(entry: ConfigEntry) -> int:
    return entry.options.get(
        CONF_HISTORY_MONTHS, entry.data.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS)
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    def entries() -> list[PzuConfigEntry]:
        return [e for e in hass.config_entries.async_entries(DOMAIN) if hasattr(e, "runtime_data")]

    async def refresh(call: ServiceCall) -> None:
        for entry in entries():
            await entry.runtime_data.coordinator.async_refresh_now()

    async def backfill_history(call: ServiceCall) -> None:
        for entry in entries():
            months = call.data.get("months") or history_months(entry)
            entry.async_create_background_task(
                hass, entry.runtime_data.backfill.async_run(months), "opcom_pzu_backfill"
            )

    async def get_prices(call: ServiceCall) -> ServiceResponse:
        start: date = call.data["start_date"]
        end: date = call.data.get("end_date") or start
        if end < start or (end - start).days >= MAX_RANGE_DAYS:
            raise ServiceValidationError(
                f"end_date must not precede start_date, and the range is limited to {MAX_RANGE_DAYS} days"
            )
        entry = next(iter(entries()), None)
        if entry is None:
            raise ServiceValidationError("OPCOM PZU is not set up")
        c = entry.runtime_data.coordinator
        store = entry.runtime_data.store
        tz = dt_util.get_default_time_zone()
        window = (local_day_bounds(start, tz)[0], local_day_bounds(end, tz)[1])
        days = (store.get(start + timedelta(days=n)) for n in range(-1, (end - start).days + 2))
        points = Series(d for d in days if d).window(*window)
        return {
            "unit": f"{c.currency}/kWh",
            "prices": [
                {
                    "start": dt_util.as_local(p.start).isoformat(),
                    "end": dt_util.as_local(p.end).isoformat(),
                    "spot": round(p.pzu, 5),
                    "buy": round(c.buy(p), 5),
                    "sell": round(c.sell(p), 5),
                }
                for p in points
            ],
        }

    hass.services.async_register(DOMAIN, "refresh", refresh)
    hass.services.async_register(
        DOMAIN,
        "backfill_history",
        backfill_history,
        vol.Schema({vol.Optional("months"): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_HISTORY_MONTHS))}),
    )
    hass.services.async_register(
        DOMAIN,
        "get_prices",
        get_prices,
        vol.Schema({vol.Required("start_date"): cv.date, vol.Optional("end_date"): cv.date}),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PzuConfigEntry) -> bool:
    client = OpcomClient(async_get_clientsession(hass), CURRENCIES[entry.data[CONF_CURRENCY]])
    store = PriceStore(hass, entry.entry_id)
    coordinator = PzuCoordinator(hass, entry, client, store)

    def progress_entity_id() -> str | None:
        return er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_history_progress"
        )

    backfill = Backfill(hass, client, store, coordinator.currency, progress_entity_id)
    entry.runtime_data = RuntimeData(coordinator, backfill, store)

    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))

    @callback
    def _start_backfill(_hass: HomeAssistant) -> None:
        entry.async_create_background_task(
            hass, backfill.async_run(history_months(entry)), "opcom_pzu_backfill"
        )

    entry.async_on_unload(async_at_started(hass, _start_backfill))
    return True


async def _reload(hass: HomeAssistant, entry: PzuConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PzuConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
