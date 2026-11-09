"""Integration tests against a real Home Assistant test instance."""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.opcom_pzu import history
from custom_components.opcom_pzu.const import DOMAIN

from .conftest import day_url, publish


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_prices(hass: HomeAssistant, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")  # before the 13:15 first try
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)

    spot = hass.states.get("sensor.opcom_pzu_spot_price")
    assert spot is not None and spot.state not in ("unknown", "unavailable")
    assert spot.attributes["unit_of_measurement"] == "RON/kWh"
    # 12:00 local = 11:00 CET = interval 45 (1-based) of the CSV
    assert hass.states.get("sensor.opcom_pzu_average_spot_price_today").state != "unknown"
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "off"
    assert hass.states.get("sensor.opcom_pzu_average_spot_price_tomorrow").state == "unknown"
    # Tomorrow's file is not requested before 13:15 local.
    assert not any("10/11/2026" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_tomorrow_polling_after_1315(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 13:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "off"

    # OPCOM publishes; the next poll (15 min + jitter) must pick it up.
    aioclient_mock.clear_requests()
    publish(aioclient_mock, date(2026, 11, 10))
    freezer.move_to("2026-11-09 13:40:00+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "on"
    assert float(hass.states.get("sensor.opcom_pzu_average_spot_price_tomorrow").state) > 0
    prices = hass.states.get("sensor.opcom_pzu_prices_tomorrow")
    assert len(prices.attributes["prices"]) == 96


async def test_option_changes_prices_and_battery(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10))
    hass.config_entries.async_update_entry(
        entry,
        options={"buy_vat_pct": 21.0, "buy_fixed_charges": 0.4, "sell_commission_pct": 10.0,
                 "battery_degradation_per_kwh": 0.05},
    )
    await setup(hass, entry)
    spot = float(hass.states.get("sensor.opcom_pzu_spot_price").state)
    buy = float(hass.states.get("sensor.opcom_pzu_buy_price").state)
    sell = float(hass.states.get("sensor.opcom_pzu_sell_price").state)
    assert buy == pytest.approx((spot + 0.4) * 1.21, abs=1e-3)
    assert sell == pytest.approx(spot * 0.9, abs=1e-3)
    gain = hass.states.get("sensor.opcom_pzu_battery_net_gain_today")
    assert gain.attributes["profitable"] is True and float(gain.state) > 0
    per_kwh = float(hass.states.get("sensor.opcom_pzu_battery_net_gain_per_kwh_today").state)
    assert per_kwh == pytest.approx(gain.attributes["gain_per_kwh_sold"], abs=1e-3)
    schedule = gain.attributes["schedule"]
    assert {b["action"] for b in schedule} == {"charge", "discharge"}
    assert hass.states.get("sensor.opcom_pzu_battery_action").state in ("charge", "discharge", "idle")


async def test_get_prices_service(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    res = await hass.services.async_call(
        DOMAIN, "get_prices", {"start_date": "2026-11-09"}, blocking=True, return_response=True
    )
    assert res["unit"] == "RON/kWh" and len(res["prices"]) == 96
    assert res["prices"][0]["start"].startswith("2026-11-09T00:00:00+02:00")


async def test_backfill_logs_and_imports(hass, entry, aioclient_mock, freezer, monkeypatch):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    monkeypatch.setattr(history, "BACKFILL_DELAY", 0)
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    for n in range(1, 40):
        publish(aioclient_mock, date(2026, 11, 8) - timedelta(days=n))
    hass.config_entries.async_update_entry(entry, data={**entry.data, "history_months": 1})
    events = []
    hass.bus.async_listen("logbook_entry", lambda e: events.append(e.data["message"]))
    await setup(hass, entry)
    await hass.services.async_call(DOMAIN, "backfill_history", {"months": 1}, blocking=True)
    for _ in range(5000):
        await hass.async_block_till_done()
        await asyncio.sleep(0)
        if any("History ready" in m for m in events):
            break
    store = entry.runtime_data.store
    assert store.has(date(2026, 10, 15)) and store.has(date(2026, 11, 7))
    # 2026-10-25 had 100 intervals; the mock serves 96, so that day is skipped, not fatal.
    assert not store.has(date(2026, 10, 25)) and store.is_missing(date(2026, 10, 25))
    assert any("Skipped 2026-10-25" in m for m in events)
    assert any("History download started" in m for m in events)
    assert any("History ready" in m for m in events)
    progress = hass.states.get("sensor.opcom_pzu_history_download_progress")
    assert progress.state == "100"


async def test_battery_uses_real_soc_and_capacity(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9))
    hass.states.async_set("sensor.my_soc", "80", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.my_cap", "12000", {"unit_of_measurement": "Wh"})
    hass.config_entries.async_update_entry(
        entry,
        options={"battery_soc_entity": "sensor.my_soc", "battery_capacity_entity": "sensor.my_cap"},
    )
    await setup(hass, entry)
    attrs = hass.states.get("sensor.opcom_pzu_battery_net_gain_today").attributes
    assert attrs["capacity_kwh"] == 12.0  # 12000 Wh from the sensor, not the 10 kWh default
    assert attrs["starting_energy_kwh"] == pytest.approx(9.6, abs=0.01)  # 80 % of 12 kWh
    assert attrs["starting_energy"] == "sensor"
    tomorrow_info = entry.runtime_data.coordinator.plan_info
    assert 0 in tomorrow_info and tomorrow_info[0]["starting_energy"] == "sensor"


async def test_battery_without_sensor_assumes_empty(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9))
    await setup(hass, entry)
    attrs = hass.states.get("sensor.opcom_pzu_battery_net_gain_today").attributes
    assert attrs["starting_energy"] == "assumed empty" and attrs["starting_energy_kwh"] == 0


async def test_session_never_keeps_cookies(hass):
    """opcom.ro answers 403 to sessions that replay its XSRF cookies."""
    import aiohttp

    from custom_components.opcom_pzu.session import create_session

    session = create_session(hass)
    assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)


async def test_upcoming_prices_roll_forward(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")  # 11:00 CET = interval 45 of 96
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    state = hass.states.get("sensor.opcom_pzu_upcoming_prices")
    rows = state.attributes["prices"]
    assert int(state.state) == len(rows) == 52  # 11:00 CET to the end of the delivery day
    assert rows[0]["start"].startswith("2026-11-09T12:00:00+02:00")  # the current interval first
    assert rows[-1]["end"].startswith("2026-11-10T01:00:00+02:00")  # delivery day ends 01:00 local
    assert {"spot", "buy", "sell"} <= rows[0].keys()
    spot = float(hass.states.get("sensor.opcom_pzu_spot_price").state)
    assert rows[0]["spot"] == pytest.approx(spot, abs=1e-4)

    # Time passes: the list drops the interval that is over.
    freezer.move_to("2026-11-09 12:15:03+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    rows = hass.states.get("sensor.opcom_pzu_upcoming_prices").attributes["prices"]
    assert len(rows) == 51 and rows[0]["start"].startswith("2026-11-09T12:15:00+02:00")


async def test_statistics_follow_the_clock(hass, entry, aioclient_mock, freezer):
    """New hours reach the long-term statistics without a restart."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period
    from homeassistant.util import dt as dt_util

    freezer.move_to("2026-11-09 14:00:00+02:00")
    # Everything already published: no fetch timer runs, only the 15-minute tick.
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10))
    await setup(hass, entry)

    async def latest():
        await async_wait_recording_done(hass)
        res = await get_instance(hass).async_add_executor_job(
            statistics_during_period, hass, dt_util.utc_from_timestamp(0), None,
            {"opcom_pzu:spot_price"}, "hour", None, {"mean"},
        )
        return max((r["start"] for r in res.get("opcom_pzu:spot_price", [])), default=0)

    before = await latest()
    freezer.move_to("2026-11-09 15:00:03+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert await latest() > before


async def test_last_fetch_survives_a_restart(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    first = hass.states.get("sensor.opcom_pzu_last_price_download").state
    assert first not in ("unknown", "unavailable")
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.opcom_pzu_last_price_download").state == first


async def test_get_prices_reports_days_not_downloaded(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    res = await hass.services.async_call(
        DOMAIN, "get_prices", {"start_date": "2026-11-09", "end_date": "2026-11-12"},
        blocking=True, return_response=True,
    )
    assert res["missing_days"] == ["2026-11-11", "2026-11-12"]


async def test_backfill_skips_a_day_that_keeps_failing(hass, entry, aioclient_mock, freezer, monkeypatch):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    monkeypatch.setattr(history, "BACKFILL_DELAY", 0)

    async def no_sleep(_):
        return None

    # Only the backfill's own sleeps (retry back-off) are skipped, not the test's.
    monkeypatch.setattr(
        history, "asyncio", SimpleNamespace(sleep=no_sleep, CancelledError=asyncio.CancelledError)
    )
    aioclient_mock.get(day_url(date(2026, 11, 3)), status=500)  # first match wins
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), empty=(date(2026, 11, 10),))
    for n in range(1, 40):
        publish(aioclient_mock, date(2026, 11, 8) - timedelta(days=n))
    events = []
    hass.bus.async_listen("logbook_entry", lambda e: events.append(e.data["message"]))
    await setup(hass, entry)
    backfill = entry.runtime_data.backfill
    for _ in range(1000):  # the automatic run at startup must finish first
        if not backfill.running:
            break
        await asyncio.sleep(0)
    await backfill.async_run(1)
    await hass.async_block_till_done()  # deliver the logbook events
    store = entry.runtime_data.store
    assert any("Skipped 2026-11-03 for now" in m for m in events)
    assert store.has(date(2026, 11, 2)) and not store.is_missing(date(2026, 11, 3))
