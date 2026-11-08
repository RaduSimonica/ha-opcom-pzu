"""Persistent price history. Doubles as the offline cache."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import DayPrices, Interval, day_bounds
from .const import DOMAIN, STORAGE_VERSION


def _encode(day: DayPrices) -> dict[str, Any]:
    return {
        "s": int(day.step.total_seconds() // 60),
        "p": [i.price for i in day.intervals],
        "b": day.base,
        "k": day.peak,
        "o": day.off_peak,
    }


def _decode(day: date, raw: dict[str, Any]) -> DayPrices:
    start, _ = day_bounds(day)
    step = timedelta(minutes=raw["s"])
    return DayPrices(
        day,
        step,
        [Interval(start + step * n, p) for n, p in enumerate(raw["p"])],
        raw.get("b"),
        raw.get("k"),
        raw.get("o"),
    )


class PriceStore:
    """Prices per delivery day, in the currency the entry was set up with."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._days: dict[str, dict[str, Any]] = {}
        self._missing: set[str] = set()  # days OPCOM has no data for

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._days = data.get("days", {})
        self._missing = set(data.get("missing", []))

    async def async_save(self) -> None:
        await self._store.async_save(
            {"days": self._days, "missing": sorted(self._missing)}
        )

    def get(self, day: date) -> DayPrices | None:
        raw = self._days.get(day.isoformat())
        return _decode(day, raw) if raw else None

    def has(self, day: date) -> bool:
        return day.isoformat() in self._days

    def is_missing(self, day: date) -> bool:
        return day.isoformat() in self._missing

    def put(self, day: DayPrices) -> None:
        self._days[day.day.isoformat()] = _encode(day)
        self._missing.discard(day.day.isoformat())

    def mark_missing(self, day: date) -> None:
        self._missing.add(day.isoformat())

    def days(self) -> list[date]:
        return sorted(date.fromisoformat(d) for d in self._days)
