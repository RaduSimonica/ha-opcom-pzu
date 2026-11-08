"""Fetch scheduling and the data every entity reads from."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_utc_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import settings
from .api import MARKET_TZ, DayPrices, OpcomClient, OpcomError
from .arbitrage import BatteryParams, Plan, Slot, plan
from .const import (
    CONF_BATTERY_CAPACITY_ENTITY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_CURRENCY,
    DOMAIN,
    FIRST_FETCH,
    JITTER_SECONDS,
    RETRY_MINUTES,
)
from .history import import_statistics
from .pricing import CompiledTariff
from .series import Point, Series, local_day_bounds
from .store import PriceStore

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PzuData:
    series: Series
    last_fetch: datetime | None


class PzuCoordinator(DataUpdateCoordinator[PzuData]):
    """Keeps yesterday, today and tomorrow loaded and refreshes on schedule."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: OpcomClient,
        store: PriceStore,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self.client = client
        self.store = store
        self.currency: str = entry.data[CONF_CURRENCY]
        self.tariff: CompiledTariff = settings.tariff(entry.options).compile()
        self.battery: BatteryParams = settings.battery(entry.options)
        self.plans: dict[int, Plan] = {}  # by day offset from the local today
        self.plan_info: dict[int, dict[str, Any]] = {}  # what each plan assumed
        self.last_fetch: datetime | None = None
        self._retry_at: dict[date, datetime] = {}
        self._jitter = timedelta(seconds=random.randint(0, JITTER_SECONDS))
        self._timer: CALLBACK_TYPE | None = None
        self._built_for: date | None = None

    # -- lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        """Load the cache, fetch what is missing and start the clock ticks."""
        await self.store.async_load()
        await self.async_config_entry_first_refresh()
        self.config_entry.async_on_unload(
            async_track_utc_time_change(
                self.hass, self._tick, minute=(0, 15, 30, 45), second=2
            )
        )
        self.config_entry.async_on_unload(self._cancel_timer)

    @callback
    def _tick(self, _now: datetime) -> None:
        if dt_util.now().date() != self._built_for:
            self.hass.async_create_task(self.async_refresh())  # day rolled over
        else:
            self.hass.async_create_task(self._async_replan())  # battery level moves

    def _cancel_timer(self) -> None:
        if self._timer:
            self._timer()
            self._timer = None

    # -- fetching ------------------------------------------------------------

    def _earliest_fetch(self, day: date) -> datetime:
        """Prices for ``day`` are published around 13:00-14:00 the day before."""
        prev = day - timedelta(days=1)
        tz = dt_util.get_default_time_zone()
        first = datetime.combine(prev, FIRST_FETCH, tz)
        return first.astimezone(timezone.utc) + self._jitter

    def _wanted_days(self) -> list[date]:
        berlin = datetime.now(MARKET_TZ).date()
        return [berlin - timedelta(days=1), berlin, berlin + timedelta(days=1)]

    async def async_refresh_now(self) -> None:
        """Manual refresh: ignore the retry throttle."""
        self._retry_at.clear()
        await self.async_refresh()

    async def _async_update_data(self) -> PzuData:
        now = datetime.now(timezone.utc)
        fetched: list[DayPrices] = []
        try:
            for day in self._wanted_days():
                if self.store.has(day) or now < max(
                    self._earliest_fetch(day), self._retry_at.get(day, now)
                ):
                    continue
                try:
                    prices = await self.client.fetch_day(day)
                except OpcomError as err:
                    _LOGGER.warning("Cannot fetch prices for %s: %s", day, err)
                    prices = None
                if prices is None:
                    self._retry_at[day] = now + timedelta(minutes=RETRY_MINUTES)
                    _LOGGER.debug("No prices for %s yet, retrying later", day)
                    continue
                self.store.put(prices)
                fetched.append(prices)
                self._retry_at.pop(day, None)
                self.last_fetch = now
            if fetched:
                await self.store.async_save()
                import_statistics(self.hass, self.currency, fetched)

            series = Series(
                d for day in self._wanted_days() if (d := self.store.get(day))
            )
            if series.at(now) is None:
                raise UpdateFailed("No prices available for the current interval")
            self._built_for = dt_util.now().date()
            data = PzuData(series, self.last_fetch)
            self.data = data  # plans below read it
            await self._async_replan(notify=False)
            return data
        finally:
            self._schedule_next()

    def _schedule_next(self) -> None:
        self._cancel_timer()
        pending = [d for d in self._wanted_days() if not self.store.has(d)]
        if not pending:
            return
        when = min(
            max(self._earliest_fetch(d), self._retry_at.get(d, datetime.min.replace(tzinfo=timezone.utc)))
            for d in pending
        )
        when = max(when, datetime.now(timezone.utc) + timedelta(seconds=5))
        self._timer = async_track_point_in_utc_time(
            self.hass, self._timer_fired, when
        )

    @callback
    def _timer_fired(self, _now: datetime) -> None:
        self._timer = None
        self.hass.async_create_task(self.async_refresh())

    # -- derived values ------------------------------------------------------

    def local_points(self, offset: int, series: Series | None = None) -> list[Point] | None:
        """Published intervals of the local day ``offset`` days from today."""
        series = series or (self.data.series if self.data else None)
        if series is None:
            return None
        day = dt_util.now().date() + timedelta(days=offset)
        start, end = local_day_bounds(day, dt_util.get_default_time_zone())
        return series.covers(start, end)

    def buy(self, point: Point) -> float:
        return self.tariff.buy_price(point.pzu, dt_util.as_local(point.start).hour)

    def sell(self, point: Point) -> float:
        return self.tariff.sell_price(point.pzu, dt_util.as_local(point.start).hour)

    def _read_number(self, entity_id: str | None) -> tuple[float, str | None] | None:
        """Numeric state of a user-provided sensor, with its unit, if usable."""
        if not entity_id or (state := self.hass.states.get(entity_id)) is None:
            return None
        try:
            return float(state.state), state.attributes.get("unit_of_measurement")
        except ValueError:
            return None  # unknown / unavailable

    def _battery_inputs(self) -> tuple[BatteryParams, float, str]:
        """Battery parameters and the energy it holds now, from the user's sensors.

        Falls back to the configured capacity and an empty battery when a sensor
        is not set or not available.
        """
        opts = self.config_entry.options
        params = self.battery
        capacity = self._read_number(opts.get(CONF_BATTERY_CAPACITY_ENTITY))
        if capacity and capacity[0] > 0:
            kwh = capacity[0] / 1000 if capacity[1] == "Wh" else capacity[0]
            params = replace(params, capacity_kwh=kwh)
        soc = self._read_number(opts.get(CONF_BATTERY_SOC_ENTITY))
        if not soc:
            return params, 0.0, "assumed empty"
        value, unit = soc
        if unit == "kWh":
            energy = value
        elif unit == "Wh":
            energy = value / 1000
        else:  # percent
            energy = min(max(value, 0.0), 100.0) / 100 * params.capacity_kwh
        return params, min(max(energy, 0.0), params.capacity_kwh), "sensor"

    async def _async_replan(self, notify: bool = True) -> None:
        if self.data is None:
            return
        params, energy, source = self._battery_inputs()
        now = datetime.now(timezone.utc)
        series = self.data.series
        self.plans, self.plan_info = await self.hass.async_add_executor_job(
            self._build_plans, series, params, energy, source, now
        )
        if notify:
            self.async_update_listeners()

    def _build_plans(
        self, series: Series, params: BatteryParams, energy: float, source: str, now: datetime
    ) -> tuple[dict[int, Plan], dict[int, dict[str, Any]]]:
        """Today: the remaining intervals, starting from the real battery level.
        Tomorrow: a full day, battery assumed empty (its level then is unknown).
        """
        plans: dict[int, Plan] = {}
        info: dict[int, dict[str, Any]] = {}
        for offset in (0, 1):
            points = self.local_points(offset, series)
            if offset == 0:
                points = [p for p in points or [] if p.end > now]
            if not points:
                continue
            slots = [Slot(p.start, self.buy(p), self.sell(p)) for p in points]
            hours = (points[0].end - points[0].start).total_seconds() / 3600
            start_energy = energy if offset == 0 else 0.0
            plans[offset] = plan(slots, hours, params, start_energy)
            info[offset] = {
                "capacity_kwh": round(params.capacity_kwh, 2),
                "starting_energy_kwh": round(start_energy, 2),
                "starting_energy": source if offset == 0 else "assumed empty",
            }
        return plans, info
