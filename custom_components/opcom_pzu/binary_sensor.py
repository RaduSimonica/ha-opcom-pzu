"""Binary sensors for automations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_CHEAP_PERCENTILE, DEFAULT_CHEAP_PERCENTILE
from .coordinator import PzuCoordinator
from .entity import PzuEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PzuBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[PzuCoordinator, int], bool | None]


def _rank(c: PzuCoordinator, cheap: bool, percentile: int) -> bool | None:
    """Is the current buy price among the cheapest/dearest N% of today?"""
    pts = c.local_points(0)
    now = c.data.series.at(datetime.now(timezone.utc))
    if not pts or not now:
        return None
    prices = sorted(c.buy(p) for p in pts)
    idx = max(0, round(len(prices) * percentile / 100) - 1)
    current = c.buy(now)
    return current <= prices[idx] if cheap else current >= prices[-1 - idx]


DESCRIPTIONS = (
    PzuBinaryDescription(
        key="tomorrow_published",
        value_fn=lambda c, _p: c.local_points(1) is not None,
    ),
    PzuBinaryDescription(key="cheap_now", value_fn=lambda c, p: _rank(c, True, p)),
    PzuBinaryDescription(key="expensive_now", value_fn=lambda c, p: _rank(c, False, p)),
    PzuBinaryDescription(
        key="battery_profitable_today",
        value_fn=lambda c, _p: (pl.profitable if (pl := c.plans.get(0)) else None),
    ),
    PzuBinaryDescription(
        key="battery_profitable_tomorrow",
        value_fn=lambda c, _p: (pl.profitable if (pl := c.plans.get(1)) else None),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PzuCoordinator = entry.runtime_data.coordinator
    async_add_entities(PzuBinarySensor(coordinator, d) for d in DESCRIPTIONS)


class PzuBinarySensor(PzuEntity, BinarySensorEntity):
    entity_description: PzuBinaryDescription

    def __init__(self, coordinator: PzuCoordinator, description: PzuBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        percentile = self.coordinator.config_entry.options.get(
            CONF_CHEAP_PERCENTILE, DEFAULT_CHEAP_PERCENTILE
        )
        return self.entity_description.value_fn(self.coordinator, percentile)
