"""Integration tests against a real Home Assistant test instance."""

import asyncio
from datetime import date, timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.opcom_pzu import history
from custom_components.opcom_pzu.const import DOMAIN

from .conftest import publish


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
