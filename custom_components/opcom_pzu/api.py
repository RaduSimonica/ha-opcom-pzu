"""OPCOM PZU (day-ahead market) CSV client and parser.

OPCOM publishes one CSV per delivery day. The market runs on CET
(Europe/Berlin): interval 1 starts at 00:00 CET, so the delivery day is
01:00-01:00 in Romania. The number of intervals varies (24/96 on a normal
day, 23/92 and 25/100 on DST days), so interval start times are derived from
the real length of the Berlin day, never from a fixed 15/60 minute grid.

Before 2025-10-01 OPCOM only served hourly data, whatever ``resolution`` is
requested, so the interval length is derived from the row count as well.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

MARKET_TZ = ZoneInfo("Europe/Berlin")
URL_TEMPLATE = (
    "https://www.opcom.ro/rapoarte-pzu-raportPIP-export-csv/"
    "{day:%d}/{day:%m}/{day:%Y}/{lang}?resolution=15"
)
USER_AGENT = "ha-opcom-pzu (Home Assistant custom integration)"
REQUEST_TIMEOUT = 30
VALID_STEP_MINUTES = (15, 30, 60)


class OpcomError(Exception):
    """OPCOM could not be reached or returned something unusable."""


class OpcomParseError(OpcomError):
    """The CSV did not look like a PZU report."""


@dataclass(frozen=True, slots=True)
class Interval:
    """One PZU delivery interval. Prices are in currency/MWh."""

    start: datetime  # timezone-aware, UTC
    price: float
    volume: float | None = None


@dataclass(slots=True)
class DayPrices:
    """All intervals of one delivery day (Berlin calendar day)."""

    day: date
    step: timedelta
    intervals: list[Interval] = field(default_factory=list)
    base: float | None = None
    peak: float | None = None
    off_peak: float | None = None

    @property
    def end(self) -> datetime:
        return self.intervals[-1].start + self.step


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Return (start, end) of a Berlin calendar day as UTC datetimes."""
    start = datetime(day.year, day.month, day.day, tzinfo=MARKET_TZ)
    nxt = day + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=MARKET_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _num(text: str) -> float:
    return float(text.strip().replace(",", "."))


def parse_csv(text: str, day: date) -> DayPrices:
    """Parse a PZU export. ``day`` is the delivery day requested in the URL."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if r]
    summary: dict[str, float] = {}
    prices: list[tuple[float, float | None]] = []
    in_table = False

    for row in rows:
        first = row[0].strip()
        if first.startswith("Zona de tranzactionare") or first.startswith("Trading zone"):
            in_table = True
            continue
        if in_table:
            try:
                prices.append((_num(row[2]), _num(row[3]) if len(row) > 3 else None))
            except (ValueError, IndexError) as err:
                raise OpcomParseError(f"Bad interval row {row!r}") from err
        elif first.startswith("ROPEX_DAM_"):
            key = (
                "base" if "Base" in first
                else "off_peak" if "Off_Peak" in first
                else "peak" if "Peak" in first
                else None
            )
            if key:
                try:
                    summary[key] = _num(row[1])
                except (ValueError, IndexError):
                    pass

    if not prices:
        raise OpcomParseError("No interval rows found")

    start, end = day_bounds(day)
    step = (end - start) / len(prices)
    if step.total_seconds() / 60 not in VALID_STEP_MINUTES:
        raise OpcomParseError(
            f"{len(prices)} intervals do not fit a {end - start} day"
        )

    return DayPrices(
        day=day,
        step=step,
        intervals=[
            Interval(start + step * i, price, volume)
            for i, (price, volume) in enumerate(prices)
        ],
        base=summary.get("base"),
        peak=summary.get("peak"),
        off_peak=summary.get("off_peak"),
    )


class OpcomClient:
    """Fetches PZU reports. No authentication or cookies are required."""

    def __init__(self, session: aiohttp.ClientSession, lang: str = "ro") -> None:
        self._session = session
        self._lang = lang

    async def fetch_day(self, day: date) -> DayPrices | None:
        """Return the prices for ``day``, or None if not published yet.

        OPCOM answers HTTP 200 with an empty body for days without results.
        """
        url = URL_TEMPLATE.format(day=day, lang=self._lang)
        try:
            async with self._session.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    raise OpcomError(f"HTTP {resp.status} for {day}")
                body = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OpcomError(f"Cannot reach OPCOM: {err}") from err

        if not body.strip():
            return None
        head = body[:300].lower()
        if b"<html" in head or b"<!doctype" in head:
            raise OpcomError("OPCOM returned a web page instead of a CSV")
        for encoding in ("utf-8-sig", "cp1250"):
            try:
                return parse_csv(body.decode(encoding), day)
            except UnicodeDecodeError:
                continue
        raise OpcomParseError("Cannot decode CSV")
