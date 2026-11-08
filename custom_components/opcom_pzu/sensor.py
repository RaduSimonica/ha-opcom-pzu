"""Sensors: prices, daily statistics, price series, battery plan, diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

from .arbitrage import Plan
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


def _blocks(plan: Plan, step: timedelta) -> list[dict[str, Any]]:
    """Merge consecutive plan slots of the same action into windows."""
    blocks: list[dict[str, Any]] = []
    for s in plan.slots:
        last = blocks[-1] if blocks else None
        if last and last["action"] == s.action and last["_end"] == s.start:
            last["_end"] = s.start + step
            last["kwh"] += s.grid_kwh
            last["_cost"] += s.grid_kwh * s.price
        else:
            blocks.append(
                {"action": s.action, "from": s.start, "_end": s.start + step,
                 "kwh": s.grid_kwh, "_cost": s.grid_kwh * s.price}
            )
    return [
        {
            "action": b["action"],
            "from": _local(b["from"]),
            "to": _local(b["_end"]),
            "kwh": round(b["kwh"], 3),
            "avg_price": round(b["_cost"] / b["kwh"], 4),
        }
        for b in blocks
    ]


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


def _series_attrs(offset: int) -> Callable[[PzuCoordinator], dict[str, Any]]:
    def attrs(c: PzuCoordinator) -> dict[str, Any]:
        pts = c.local_points(offset) or []
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
                for p in pts
            ],
        }

    return attrs


def _plan_attrs(offset: int) -> Callable[[PzuCoordinator], dict[str, Any]]:
    def attrs(c: PzuCoordinator) -> dict[str, Any]:
        plan = c.plans.get(offset)
        pts = c.local_points(offset)
        if plan is None or not pts:
            return {}
        out: dict[str, Any] = {"profitable": plan.profitable} | c.plan_info.get(offset, {})
        if plan.profitable:
            out |= {
                "energy_bought_kwh": round(plan.energy_bought_kwh, 2),
                "energy_sold_kwh": round(plan.energy_sold_kwh, 2),
                "gain_per_kwh_sold": _round(plan.gain_per_kwh_sold),
                "gain_per_kwh_bought": _round(plan.gain_per_kwh_bought),
                "full_cycles": round(plan.cycles, 2),
                "cheapest_charge_breaks_even_at": _round(plan.break_even_sell_price),
                "schedule": _blocks(plan, pts[0].end - pts[0].start),
            }
        return out

    return attrs


def _plan_value(offset: int, field: str) -> Callable[[PzuCoordinator], float | None]:
    def value(c: PzuCoordinator) -> float | None:
        plan = c.plans.get(offset)
        if plan is None:
            return None
        return _round(getattr(plan, field) or 0.0)

    return value


def _battery_action(c: PzuCoordinator) -> str | None:
    plan = c.plans.get(0)
    if plan is None:
        return None
    now = c.data.series.at(datetime.now(timezone.utc))
    for slot in plan.slots:
        if now and slot.start == now.start:
            return slot.action
    return "idle"


def _battery_action_attrs(c: PzuCoordinator) -> dict[str, Any]:
    plan = c.plans.get(0)
    now = datetime.now(timezone.utc)
    if plan is None:
        return {}
    upcoming = next((s for s in plan.slots if s.start > now and s.action != _battery_action(c)), None)
    return (
        {"next_change": _local(upcoming.start), "next_action": upcoming.action}
        if upcoming
        else {}
    )


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
            key=f"battery_gain_{day}", unit_kind="money", device_class=SensorDeviceClass.MONETARY,
            suggested_display_precision=2,
            value_fn=_plan_value(offset, "net_gain"), attrs_fn=_plan_attrs(offset)))
        out.append(PzuSensorDescription(
            key=f"battery_gain_per_kwh_{day}", unit_kind="price", suggested_display_precision=3,
            value_fn=_plan_value(offset, "gain_per_kwh_sold")))
    out.append(PzuSensorDescription(
        key="battery_action", device_class=SensorDeviceClass.ENUM,
        options=["charge", "discharge", "idle"],
        value_fn=_battery_action, attrs_fn=_battery_action_attrs))
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
    _unrecorded_attributes = frozenset({"prices", "schedule"})

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
