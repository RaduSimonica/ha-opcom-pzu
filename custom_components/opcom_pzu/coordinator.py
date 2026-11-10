"""Fetch scheduling and the data every entity reads from."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

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
from .const import (
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
        self.last_fetch: datetime | None = None
        self._retry_at: dict[date, datetime] = {}
        self._jitter = timedelta(seconds=random.randint(0, JITTER_SECONDS))
        self._timer: CALLBACK_TYPE | None = None
        self._built_for: date | None = None
        self._built_days: list[date] = []  # the market days the last refresh planned for

    # -- lifecycle -----------------------------------------------------------

    async def async_start(self) -> None:
        """Load the cache, fetch what is missing and start the clock ticks."""
        await self.store.async_load()
        self.last_fetch = self.store.last_fetch
        # Registered first, so a failed first refresh does not leave a timer behind.
        self.config_entry.async_on_unload(self._cancel_timer)
        await self.async_config_entry_first_refresh()
        self.config_entry.async_on_unload(
            async_track_utc_time_change(
                self.hass, self._tick, minute=(0, 15, 30, 45), second=2
            )
        )

    @callback
    def _tick(self, _now: datetime) -> None:
        if self._needs_refresh():
            self.hass.async_create_task(self.async_refresh())
        else:
            self._import_recent()  # keep the long-term statistics current
            self.async_update_listeners()  # the current interval moved on

    def _needs_refresh(self) -> bool:
        """A new day began, or the market moved on to a day nobody is waiting for yet.

        The market day turns over at 01:00 local time (midnight in Berlin), an hour
        after the local date. Without this check, a Home Assistant that ran across
        that hour never scheduled a fetch for the new "tomorrow". A pending day with
        no timer set is also caught here, as a safety net.
        """
        if dt_util.now().date() != self._built_for or self._wanted_days() != self._built_days:
            return True
        return self._timer is None and any(not self.store.has(d) for d in self._wanted_days())

    def _import_recent(self) -> None:
        """Long-term statistics for the hours that have passed (idempotent)."""
        days = [d for day in self._wanted_days() if (d := self.store.get(day))]
        if days:
            import_statistics(self.hass, self.currency, days)

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
                self.last_fetch = self.store.last_fetch = now
            if fetched:
                await self.store.async_save()
            self._import_recent()

            series = Series(
                d for day in self._wanted_days() if (d := self.store.get(day))
            )
            if series.at(now) is None:
                raise UpdateFailed("No prices available for the current interval")
            self._built_for = dt_util.now().date()
            self._built_days = self._wanted_days()
            data = PzuData(series, self.last_fetch)
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
