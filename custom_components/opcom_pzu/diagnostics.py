"""What a bug report needs, in one download (Settings -> Devices & services -> three dots)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.system_info import async_get_system_info
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import PzuConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: PzuConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data.coordinator
    store = entry.runtime_data.store
    integration = await async_get_integration(hass, entry.domain)
    info = await async_get_system_info(hass)
    days = store.days()
    now = dt_util.now()
    data = coordinator.data
    upcoming = data.series.window(now, now + timedelta(days=3)) if data else []
    return {
        "integration_version": str(integration.version),
        "home_assistant": {"version": info.get("version"), "timezone": str(dt_util.get_default_time_zone())},
        "now": now.isoformat(),
        "config": dict(entry.data),
        "options": dict(entry.options),  # prices and formulas, nothing personal
        "prices": {
            "days_stored": len(days),
            "first_day": days[0].isoformat() if days else None,
            "last_day": days[-1].isoformat() if days else None,
            "today_stored": store.has(now.date()),
            "tomorrow_stored": store.has(now.date() + timedelta(days=1)),
            "intervals_from_now": len(upcoming),
            "last_fetch": coordinator.last_fetch.isoformat() if coordinator.last_fetch else None,
            "fetch_timer_set": coordinator._timer is not None,  # noqa: SLF001
        },
        "history_download": {"running": entry.runtime_data.backfill.running},
    }
