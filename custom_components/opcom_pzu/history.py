"""Long-term statistics import and the historic price backfill.

Prices are imported as hourly external statistics (mean/min/max) under
``opcom_pzu:spot_price``, so they graph natively in Home Assistant with the
statistics graph card, going back as far as the user asked for.

OPCOM serves one file per day and we are polite about it (one request per
second), so a long backfill takes a while. It runs in the background, can be
interrupted at any time, resumes where it left off, and reports progress in
the logbook.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import EVENT_LOGBOOK_ENTRY
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import DayPrices, OpcomClient, OpcomError, OpcomParseError
from .const import BACKFILL_DELAY, DOMAIN, NAME
from .store import PriceStore

try:  # Home Assistant 2025.x+
    from homeassistant.components.recorder.models import StatisticMeanType

    _MEAN: dict = {"mean_type": StatisticMeanType.ARITHMETIC}
except ImportError:  # pragma: no cover - older Home Assistant
    _MEAN = {"has_mean": True}

_LOGGER = logging.getLogger(__name__)

STATISTIC_ID = f"{DOMAIN}:spot_price"
OLDEST_DAY = date(2015, 1, 1)
SAVE_EVERY = 30  # days downloaded between saves/imports
RETRIES = 3


def import_statistics(
    hass: HomeAssistant, currency: str, days: list[DayPrices]
) -> None:
    """Queue hourly statistics for ``days`` (delivery days, prices per MWh)."""
    now = datetime.now(timezone.utc)
    hours: dict[datetime, list[float]] = defaultdict(list)
    for day in days:
        for interval in day.intervals:
            if interval.start <= now:
                hour = interval.start.replace(minute=0, second=0, microsecond=0)
                hours[hour].append(interval.price / 1000)
    if not hours:
        return
    metadata = StatisticMetaData(
        has_sum=False,
        name=f"{NAME} spot price",
        source=DOMAIN,
        statistic_id=STATISTIC_ID,
        unit_of_measurement=f"{currency}/kWh",
        unit_class=None,
        **_MEAN,
    )
    async_add_external_statistics(
        hass,
        metadata,
        [
            StatisticData(start=h, mean=sum(v) / len(v), min=min(v), max=max(v))
            for h, v in sorted(hours.items())
        ],
    )


def log_to_logbook(
    hass: HomeAssistant, message: str, entity_id: str | None = None
) -> None:
    data = {"name": NAME, "message": message, "domain": DOMAIN}
    if entity_id:
        data["entity_id"] = entity_id
    hass.bus.async_fire(EVENT_LOGBOOK_ENTRY, data)


class _Paused(Exception):
    """OPCOM kept failing; stop and resume later."""


class Backfill:
    """Background download of historic prices, observable by a sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: OpcomClient,
        store: PriceStore,
        currency: str,
        entity_id: Callable[[], str | None],
    ) -> None:
        self._hass = hass
        self._client = client
        self._store = store
        self._currency = currency
        self._entity_id = entity_id
        self._listeners: list[Callable[[], None]] = []
        self.running = False
        self.total = 0
        self.done = 0
        self.status = "idle"

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @property
    def progress(self) -> int:
        return 100 if not self.total else int(self.done * 100 / self.total)

    def _set(self, status: str) -> None:
        self.status = status
        for listener in list(self._listeners):
            listener()

    def _log(self, message: str) -> None:
        _LOGGER.info(message)
        log_to_logbook(self._hass, message, self._entity_id())

    async def async_run(self, months: int) -> None:
        if self.running:
            return
        self.running = True
        try:
            await self._run(months)
        except _Paused:
            pass  # status and logbook entry were set where it happened
        except asyncio.CancelledError:
            self._set("interrupted")
            raise
        finally:
            self.running = False

    async def _run(self, months: int) -> None:
        yesterday = dt_util.now().date() - timedelta(days=1)
        oldest = max(OLDEST_DAY, yesterday - timedelta(days=round(months * 30.44)))
        span = [oldest + timedelta(days=n) for n in range((yesterday - oldest).days + 1)]
        todo = [d for d in span if not self._store.has(d) and not self._store.is_missing(d)]
        self.total, self.done = len(todo), 0

        if todo:
            minutes = max(1, round(len(todo) * (BACKFILL_DELAY + 0.5) / 60))
            self._log(
                f"History download started: {len(todo)} days from {todo[0]} "
                f"(about {minutes} min, one request per second so OPCOM is not "
                "overloaded). It runs in the background and resumes by itself "
                "if Home Assistant restarts. Price statistics appear in graphs "
                "as the download progresses."
            )
        self._set("downloading" if todo else "importing")

        milestones = {25, 50, 75}
        batch: list[DayPrices] = []
        for day in todo:
            data = await self._fetch(day)
            if data is None:
                self._store.mark_missing(day)
            else:
                self._store.put(data)
                batch.append(data)
            self.done += 1
            self._set("downloading")
            if len(batch) >= SAVE_EVERY:
                await self._flush(batch)
                batch = []
            while milestones and self.progress >= min(milestones):
                self._log(
                    f"History download {min(milestones)}% done "
                    f"({self.done}/{self.total} days)."
                )
                milestones.discard(min(milestones))
            await asyncio.sleep(BACKFILL_DELAY)

        await self._flush(batch)
        # Make sure everything already on disk is in the statistics too.
        self._set("importing")
        stored = [d for d in span if self._store.has(d)]
        for i in range(0, len(stored), 90):
            import_statistics(
                self._hass,
                self._currency,
                [self._store.get(d) for d in stored[i : i + 90]],
            )
            await asyncio.sleep(0)
        self._set("idle")
        self._log(
            f"History ready: {len(stored)} days of prices are available as the "
            f"statistic '{STATISTIC_ID}' (use a Statistics graph card)."
        )

    async def _flush(self, batch: list[DayPrices]) -> None:
        await self._store.async_save()
        if batch:
            import_statistics(self._hass, self._currency, batch)

    async def _fetch(self, day: date) -> DayPrices | None:
        for attempt in range(1, RETRIES + 1):
            try:
                return await self._client.fetch_day(day)
            except OpcomParseError as err:
                # A broken file will not fix itself: skip the day, keep going.
                self._log(f"Skipped {day}: OPCOM's file for that day is unusable ({err}).")
                return None
            except OpcomError as err:
                if attempt == RETRIES:
                    self._set("paused")
                    self._log(
                        f"History download paused at {day}: {err}. Progress is "
                        "saved; it will continue on the next Home Assistant "
                        "start or when you call opcom_pzu.backfill_history."
                    )
                    raise _Paused from err
                await asyncio.sleep(5 * attempt)
        return None
