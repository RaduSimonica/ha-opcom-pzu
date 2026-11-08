"""Price series across delivery days, and the statistics sensors need."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Iterable

from .api import DayPrices

MWH_PER_KWH = 1000.0


@dataclass(frozen=True, slots=True)
class Point:
    """One interval. ``pzu`` is currency/kWh."""

    start: datetime  # UTC
    end: datetime
    pzu: float


@dataclass(frozen=True, slots=True)
class Stats:
    average: float
    minimum: float
    maximum: float
    min_start: datetime
    max_start: datetime


def local_day_bounds(day: date, tz: tzinfo) -> tuple[datetime, datetime]:
    """Bounds of a calendar day in ``tz`` (the Home Assistant time zone)."""
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    nxt = day + timedelta(days=1)
    return start, datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)


class Series:
    """Every known interval, sorted, with prices converted to per-kWh."""

    def __init__(self, days: Iterable[DayPrices]) -> None:
        points = [
            Point(i.start, i.start + d.step, i.price / MWH_PER_KWH)
            for d in days
            for i in d.intervals
        ]
        points.sort(key=lambda p: p.start)
        self.points = points
        self._starts = [p.start for p in points]

    def at(self, moment: datetime) -> Point | None:
        idx = bisect_right(self._starts, moment) - 1
        if idx >= 0 and self.points[idx].end > moment:
            return self.points[idx]
        return None

    def following(self, moment: datetime) -> Point | None:
        cur = self.at(moment)
        return self.at(cur.end) if cur else None

    def window(self, start: datetime, end: datetime) -> list[Point]:
        lo = bisect_right(self._starts, start - timedelta(seconds=1))
        return [p for p in self.points[lo:] if p.start < end and p.end > start]

    def covers(self, start: datetime, end: datetime) -> list[Point] | None:
        """Points of [start, end) if the whole window is published, else None."""
        pts = self.window(start, end)
        if pts and pts[0].start <= start and pts[-1].end >= end:
            return pts
        return None


def stats(points: list[Point], prices: list[float] | None = None) -> Stats | None:
    """Time-weighted average, min and max. ``prices`` overrides ``pzu``."""
    if not points:
        return None
    values = prices if prices is not None else [p.pzu for p in points]
    total = sum((p.end - p.start).total_seconds() for p in points)
    avg = sum(v * (p.end - p.start).total_seconds() for v, p in zip(values, points)) / total
    lo = min(range(len(values)), key=values.__getitem__)
    hi = max(range(len(values)), key=values.__getitem__)
    return Stats(avg, values[lo], values[hi], points[lo].start, points[hi].start)
