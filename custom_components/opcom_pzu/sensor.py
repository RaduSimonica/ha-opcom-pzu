"""Sensors: prices, daily statistics, price series, diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import PzuCoordinator
from .entity import PzuEntity
from .history import Backfill
from .series import Point, stats

PARALLEL_UPDATES = 0

KINDS = ("spot", "buy", "sell")
DAYS = {"today": 0, "tomorrow": 1}
STATS = ("average", "minimum", "maximum")


def _local(moment: datetime) -> str:
    return dt_util.as_local(moment).isoformat()


def _price_of(c: PzuCoordinator, kind: str, p: Point) -> float:
    return {"spot": p.pzu, "buy": c.buy(p), "sell": c.sell(p)}[kind]


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


@dataclass(frozen=True, kw_only=True)
class PzuSensorDescription(SensorEntityDescription):
    value_fn: Callable[[PzuCoordinator], Any]
    attrs_fn: Callable[[PzuCoordinator], dict[str, Any]] | None = None
    unit_kind: str | None = None  # "price" | "money" | None


def _current(kind: str, following: bool) -> Callable[[PzuCoordinator], float | None]:
    def value(c: PzuCoordinator) -> float | None:
        now = datetime.now(timezone.utc)
        p = c.data.series.following(now) if following else c.data.series.at(now)
        return _round(_price_of(c, kind, p)) if p else None

    return value


def _current_attrs(following: bool) -> Callable[[PzuCoordinator], dict[str, Any]]:
    def attrs(c: PzuCoordinator) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        p = c.data.series.following(now) if following else c.data.series.at(now)
        return {"interval_start": _local(p.start), "interval_end": _local(p.end)} if p else {}

    return attrs


def _stat(kind: str, offset: int, field: str) -> Callable[[PzuCoordinator], float | None]:
    def value(c: PzuCoordinator) -> float | None:
        pts = c.local_points(offset)
        s = stats(pts, [_price_of(c, kind, p) for p in pts]) if pts else None
        return _round(getattr(s, field)) if s else None

    return value


def _stat_attrs(kind: str, offset: int, field: str) -> Callable[[PzuCoordinator], dict[str, Any]]:
    def attrs(c: PzuCoordinator) -> dict[str, Any]:
        pts = c.local_points(offset)
        s = stats(pts, [_price_of(c, kind, p) for p in pts]) if pts else None
        if not s or field == "average":
            return {}
        return {"at": _local(s.min_start if field == "minimum" else s.max_start)}

    return attrs


def _price_rows(c: PzuCoordinator, points: list[Point]) -> dict[str, Any]:
    """The attributes of a price series, ready for charts."""
    return {
        "unit": f"{c.currency}/kWh",
        "prices": [
            {
                "start": _local(p.start),
                "end": _local(p.end),
                "spot": round(p.pzu, 5),
                "buy": round(c.buy(p), 5),
                "sell": round(c.sell(p), 5),
            }
            for p in points
        ],
    }


def _series_attrs(offset: int) -> Callable[[PzuCoordinator], dict[str, Any]]:
    return lambda c: _price_rows(c, c.local_points(offset) or [])


def _upcoming(c: PzuCoordinator) -> list[Point]:
    """Every known interval from the current one onwards (today, then tomorrow)."""
    now = datetime.now(timezone.utc)
    return [p for p in c.data.series.points if p.end > now]


def _descriptions() -> list[PzuSensorDescription]:
    out: list[PzuSensorDescription] = []
    for kind in KINDS:
        out.append(PzuSensorDescription(
            key=f"{kind}_price", unit_kind="price",
            state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=3,
            value_fn=_current(kind, False), attrs_fn=_current_attrs(False)))
        out.append(PzuSensorDescription(
            key=f"next_{kind}_price", unit_kind="price", suggested_display_precision=3,
            entity_registry_enabled_default=kind == "spot",
            value_fn=_current(kind, True), attrs_fn=_current_attrs(True)))
        for day, offset in DAYS.items():
            for field in STATS:
                out.append(PzuSensorDescription(
                    key=f"{kind}_{field}_{day}", unit_kind="price", suggested_display_precision=3,
                    entity_registry_enabled_default=kind == "spot",
                    value_fn=_stat(kind, offset, field), attrs_fn=_stat_attrs(kind, offset, field)))
    for day, offset in DAYS.items():
        out.append(PzuSensorDescription(
            key=f"prices_{day}", entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=lambda c, o=offset: len(c.local_points(o) or []) or None,
            attrs_fn=_series_attrs(offset)))
    out.append(PzuSensorDescription(
        key="upcoming_prices", entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: len(_upcoming(c)) or None,
        attrs_fn=lambda c: _price_rows(c, _upcoming(c))))
    out.append(PzuSensorDescription(
        key="last_fetch", device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.last_fetch))
    return out


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator: PzuCoordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [PzuSensor(coordinator, d) for d in _descriptions()]
    entities.append(BackfillSensor(coordinator, entry.runtime_data.backfill))
    async_add_entities(entities)


class PzuSensor(PzuEntity, SensorEntity):
    entity_description: PzuSensorDescription
    _unrecorded_attributes = frozenset({"prices"})

    def __init__(self, coordinator: PzuCoordinator, description: PzuSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        kind = self.entity_description.unit_kind
        cur = self.coordinator.currency
        return {"price": f"{cur}/kWh", "money": cur}.get(kind) if kind else None

    @property
    def native_value(self) -> Any:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn and self.coordinator.data else None


class BackfillSensor(PzuEntity, SensorEntity):
    """Progress of the historic download. Always available, unlike prices."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:history"

    def __init__(self, coordinator: PzuCoordinator, backfill: Backfill) -> None:
        super().__init__(coordinator, "history_progress")
        self._backfill = backfill

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._backfill.add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> int:
        return self._backfill.progress

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        b = self._backfill
        return {"status": b.status, "days_done": b.done, "days_total": b.total}
