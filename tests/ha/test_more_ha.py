"""More Home Assistant level tests: client, store, services, setup failures."""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.opcom_pzu import history
from custom_components.opcom_pzu.api import (
    OpcomClient,
    OpcomError,
    OpcomHTTPError,
    OpcomParseError,
    parse_csv,
)
from custom_components.opcom_pzu.const import DOMAIN
from custom_components.opcom_pzu.session import create_session
from custom_components.opcom_pzu.store import PriceStore

from .conftest import CSV, day_url, publish

DAY = date(2026, 11, 9)


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# -- client --------------------------------------------------------------------


async def test_client_unpublished_day_is_none(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), text="  \n")
    assert await OpcomClient(create_session(hass)).fetch_day(DAY) is None


async def test_client_reads_a_published_day_and_sends_a_user_agent(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), text=CSV)
    d = await OpcomClient(create_session(hass)).fetch_day(DAY)
    assert len(d.intervals) == 96
    assert "ha-opcom-pzu" in aioclient_mock.mock_calls[0][3]["User-Agent"]


async def test_client_uses_the_language_for_the_currency(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY, "en"), text=CSV)
    await OpcomClient(create_session(hass), "en").fetch_day(DAY)


async def test_client_http_error(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), status=503)
    with pytest.raises(OpcomHTTPError):
        await OpcomClient(create_session(hass)).fetch_day(DAY)


async def test_client_html_page_instead_of_csv(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), text="<!DOCTYPE html><html>maintenance</html>")
    with pytest.raises(OpcomError):
        await OpcomClient(create_session(hass)).fetch_day(DAY)


async def test_client_network_error(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), exc=aiohttp.ClientConnectionError("boom"))
    with pytest.raises(OpcomError):
        await OpcomClient(create_session(hass)).fetch_day(DAY)


async def test_client_timeout(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), exc=TimeoutError())
    with pytest.raises(OpcomError):
        await OpcomClient(create_session(hass)).fetch_day(DAY)


async def test_client_decodes_cp1250(hass, aioclient_mock):
    body = CSV.replace("Romania", "România").encode("cp1250")
    aioclient_mock.get(day_url(DAY), content=body)
    assert len((await OpcomClient(create_session(hass)).fetch_day(DAY)).intervals) == 96


async def test_client_unparseable_file(hass, aioclient_mock):
    aioclient_mock.get(day_url(DAY), text='"nothing useful"\n')
    with pytest.raises(OpcomParseError):
        await OpcomClient(create_session(hass)).fetch_day(DAY)


# -- store ---------------------------------------------------------------------


async def test_store_round_trip(hass):
    from datetime import datetime, timezone

    store = PriceStore(hass, "abc")
    await store.async_load()
    day = parse_csv(CSV, DAY)
    store.put(day)
    store.mark_missing(date(2020, 1, 1))
    store.last_fetch = datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc)
    await store.async_save()

    again = PriceStore(hass, "abc")
    await again.async_load()
    back = again.get(DAY)
    assert back.step == day.step and [i.price for i in back.intervals] == [i.price for i in day.intervals]
    assert back.intervals[0].start == day.intervals[0].start
    assert (back.base, back.peak, back.off_peak) == (day.base, day.peak, day.off_peak)
    assert again.is_missing(date(2020, 1, 1)) and again.days() == [DAY]
    assert again.last_fetch == store.last_fetch


async def test_store_put_clears_missing(hass):
    store = PriceStore(hass, "abc")
    store.mark_missing(DAY)
    store.put(parse_csv(CSV, DAY))
    assert store.has(DAY) and not store.is_missing(DAY)
    assert store.get(date(2000, 1, 1)) is None


# -- setup failures ------------------------------------------------------------


async def test_failed_setup_retries_and_leaves_no_timer(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    aioclient_mock.get(day_url(DAY), status=500)
    aioclient_mock.get(day_url(date(2026, 11, 8)), status=500)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.runtime_data.coordinator._timer is None


async def test_unload_closes_the_session_and_timer(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    coordinator = entry.runtime_data.coordinator
    assert coordinator._timer is not None  # waiting to look for tomorrow's prices
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert coordinator.client._session.closed and coordinator._timer is None


async def test_reload_does_not_stack_sessions(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    sessions = []
    for _ in range(3):
        sessions.append(entry.runtime_data.coordinator.client._session)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert all(s.closed for s in sessions)


# -- services ------------------------------------------------------------------


async def test_get_prices_rejects_bad_ranges(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    for data in (
        {"start_date": "2026-11-09", "end_date": "2026-11-01"},
        {"start_date": "2024-01-01", "end_date": "2026-11-09"},
    ):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, "get_prices", data, blocking=True, return_response=True)


async def test_get_prices_single_day_has_buy_and_sell(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    hass.config_entries.async_update_entry(entry, options={"buy_vat_pct": 0, "sell_commission_pct": 50})
    await setup(hass, entry)
    res = await hass.services.async_call(DOMAIN, "get_prices", {"start_date": "2026-11-09"}, blocking=True, return_response=True)
    row = res["prices"][10]
    assert row["buy"] == pytest.approx(row["spot"], abs=1e-4)
    assert row["sell"] == pytest.approx(row["spot"] / 2, abs=1e-4)
    assert res["missing_days"] == []


async def test_backfill_service_rejects_bad_months(hass, entry, aioclient_mock, freezer):
    import voluptuous as vol

    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "backfill_history", {"months": 0}, blocking=True)


async def test_refresh_service_fetches_tomorrow_early(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")  # before the scheduled first try
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "off"
    aioclient_mock.clear_requests()
    publish(aioclient_mock, date(2026, 11, 10))
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    # A manual refresh respects the "not before 13:15" rule: no request yet...
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "off"
    freezer.move_to("2026-11-09 14:00:00+02:00")
    await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    await hass.async_block_till_done()
    # ...and after it, ignores the retry throttle.
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "on"


# -- backfill ------------------------------------------------------------------


async def test_backfill_pauses_when_opcom_keeps_failing(hass, entry, aioclient_mock, freezer, monkeypatch):
    freezer.move_to("2026-11-09 12:00:00+02:00")

    async def no_sleep(_):
        return None

    monkeypatch.setattr(history, "asyncio", SimpleNamespace(sleep=no_sleep, CancelledError=asyncio.CancelledError))
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    for n in range(1, 40):
        aioclient_mock.get(day_url(date(2026, 11, 8) - timedelta(days=n)), status=503)
    events = []
    hass.bus.async_listen("logbook_entry", lambda e: events.append(e.data["message"]))
    await setup(hass, entry)
    backfill = entry.runtime_data.backfill
    for _ in range(1000):
        if not backfill.running:
            break
        await asyncio.sleep(0)
    await backfill.async_run(1)
    await hass.async_block_till_done()
    assert backfill.status == "paused"
    assert any("keeps answering with errors" in m for m in events)
    store = entry.runtime_data.store
    assert not any(store.is_missing(date(2026, 11, 8) - timedelta(days=n)) for n in range(1, 40))


async def test_backfill_does_not_run_twice_at_once(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    b = entry.runtime_data.backfill
    b.running = True
    await b.async_run(1)  # returns at once, does not start a second download
    assert b.status in ("idle", "importing", "downloading", "paused")
    b.running = False


# -- binary sensors ------------------------------------------------------------


async def test_cheap_and_expensive_now_follow_the_price(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:30+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    await setup(hass, entry)
    prices = [r["buy"] for r in hass.states.get("sensor.opcom_pzu_prices_today").attributes["prices"]]
    now_buy = float(hass.states.get("sensor.opcom_pzu_spot_price").state) * 1.21
    cheap = hass.states.get("binary_sensor.opcom_pzu_cheap_price_now").state
    dear = hass.states.get("binary_sensor.opcom_pzu_expensive_price_now").state
    ranked = sorted(prices)
    assert cheap == ("on" if now_buy <= ranked[23] + 1e-3 else "off")
    assert dear == ("on" if now_buy >= ranked[-24] - 1e-3 else "off")
    assert not (cheap == "on" and dear == "on")


async def test_flat_day_is_neither_cheap_nor_expensive(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:30+02:00")
    flat = CSV
    import re

    flat = re.sub(r'("Romania","\d+",)"[\d.]+"', r'\1"100.0"', flat)
    aioclient_mock.get(day_url(date(2026, 11, 8)), text=flat)
    aioclient_mock.get(day_url(DAY), text=flat)
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.opcom_pzu_cheap_price_now").state == "off"
    assert hass.states.get("binary_sensor.opcom_pzu_expensive_price_now").state == "off"


async def test_cheap_percentile_is_configurable(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:30+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    hass.config_entries.async_update_entry(entry, options={"cheap_percentile": 50})
    await setup(hass, entry)
    prices = sorted(r["buy"] for r in hass.states.get("sensor.opcom_pzu_prices_today").attributes["prices"])
    now_buy = float(hass.states.get("sensor.opcom_pzu_spot_price").state) * 1.21
    assert hass.states.get("binary_sensor.opcom_pzu_cheap_price_now").state == ("on" if now_buy <= prices[47] + 1e-3 else "off")


# -- sensors -------------------------------------------------------------------


async def test_daily_statistics_match_the_csv(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:30+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    await setup(hass, entry)
    rows = parse_csv(CSV, DAY).intervals
    prices = [i.price / 1000 for i in rows]
    # The local day starts one hour before the delivery day (01:00 local), so the
    # statistics cover 23:00-24:00 of the previous day's file too: bounds, not equality.
    assert float(hass.states.get("sensor.opcom_pzu_highest_spot_price_today").state) >= max(prices[1:]) - 1e-3
    assert float(hass.states.get("sensor.opcom_pzu_lowest_spot_price_today").state) <= min(prices[1:]) + 1e-3
    assert "at" in hass.states.get("sensor.opcom_pzu_highest_spot_price_today").attributes


async def test_current_price_changes_at_the_quarter_hour(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:30+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    await setup(hass, entry)
    first = hass.states.get("sensor.opcom_pzu_spot_price")
    nxt = hass.states.get("sensor.opcom_pzu_next_spot_price")
    freezer.move_to("2026-11-09 12:15:03+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    now = hass.states.get("sensor.opcom_pzu_spot_price")
    assert now.attributes["interval_start"].startswith("2026-11-09T12:15")
    assert now.state == nxt.state and first.attributes["interval_start"] != now.attributes["interval_start"]


async def test_midnight_rolls_the_day_over(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 23:50:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, date(2026, 11, 10))
    aioclient_mock.get(day_url(date(2026, 11, 11)), text="")
    await setup(hass, entry)
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "on"
    freezer.move_to("2026-11-10 00:00:05+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    # The 10th is "today" now, and 11th is not published.
    assert hass.states.get("sensor.opcom_pzu_spot_price").state not in ("unknown", "unavailable")
    assert hass.states.get("binary_sensor.opcom_pzu_tomorrow_s_prices_published").state == "off"


# -- options flow ----------------------------------------------------------------


async def _open(hass, entry, aioclient_mock, freezer, step):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    await setup(hass, entry)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": step})


async def _finish(hass, flow):
    assert flow["type"] is FlowResultType.MENU
    done = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "finish"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_general_page_saves_integers(hass, entry, aioclient_mock, freezer):
    form = await _open(hass, entry, aioclient_mock, freezer, "general")
    flow = await hass.config_entries.options.async_configure(
        form["flow_id"], {"history_months": 0.0, "cheap_percentile": 30.0}
    )
    await _finish(hass, flow)
    assert entry.options["history_months"] == 0 and entry.options["cheap_percentile"] == 30
    assert isinstance(entry.options["history_months"], int)


async def test_options_are_not_saved_until_finish(hass, entry, aioclient_mock, freezer):
    form = await _open(hass, entry, aioclient_mock, freezer, "buy")
    flow = await hass.config_entries.options.async_configure(form["flow_id"], {"buy_vat_pct": 9})
    assert flow["type"] is FlowResultType.MENU and "buy_vat_pct" not in entry.options
    hass.config_entries.options.async_abort(flow["flow_id"])
    assert "buy_vat_pct" not in entry.options


async def test_invalid_formula_keeps_what_was_typed_and_shows_the_error(hass, entry, aioclient_mock, freezer):
    form = await _open(hass, entry, aioclient_mock, freezer, "buy")
    bad = await hass.config_entries.options.async_configure(form["flow_id"], {"buy_formula": "pzu +"})
    assert bad["type"] is FlowResultType.FORM and bad["errors"] == {"buy_formula": "invalid_formula"}
    assert bad["description_placeholders"]["error"]
    ok = await hass.config_entries.options.async_configure(bad["flow_id"], {"buy_formula": "pzu * 1.5"})
    await _finish(hass, ok)
    assert entry.options["buy_formula"] == "pzu * 1.5"
    # The new formula is used.
    spot = float(hass.states.get("sensor.opcom_pzu_spot_price").state)
    prices = hass.states.get("sensor.opcom_pzu_prices_today").attributes["prices"]
    assert any(r["buy"] == pytest.approx(r["spot"] * 1.5, abs=1e-4) for r in prices) and spot > 0


async def test_cleared_buy_fields_reset_to_zero(hass, entry, aioclient_mock, freezer):
    hass.config_entries.async_update_entry(entry, options={"buy_formula": "pzu*2", "buy_markup_pct": 7, "buy_fixed_charges": 0.3})
    form = await _open(hass, entry, aioclient_mock, freezer, "buy")
    flow = await hass.config_entries.options.async_configure(form["flow_id"], {"buy_vat_pct": 21})
    await _finish(hass, flow)
    assert entry.options["buy_formula"] == "" and entry.options["buy_markup_pct"] == 0.0
    assert entry.options["buy_fixed_charges"] == 0.0 and entry.options["buy_vat_pct"] == 21


async def test_second_entry_is_refused(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY)
    await setup(hass, entry)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT


# -- 0.3.0: the battery planner moved to Battery Planner ---------------------------


async def test_old_battery_entities_and_options_are_removed(hass, entry, aioclient_mock, freezer):
    from homeassistant.helpers import entity_registry as er

    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    registry = er.async_get(hass)
    old = {
        ("sensor", "battery_gain_today"), ("sensor", "battery_gain_tomorrow"),
        ("sensor", "battery_gain_per_kwh_today"), ("sensor", "battery_gain_per_kwh_tomorrow"),
        ("sensor", "battery_action"), ("binary_sensor", "battery_profitable_today"),
        ("binary_sensor", "battery_profitable_tomorrow"),
    }
    for domain, key in old:
        registry.async_get_or_create(domain, DOMAIN, f"{entry.entry_id}_{key}", config_entry=entry)
    hass.config_entries.async_update_entry(
        entry,
        options={
            "buy_vat_pct": 9.0, "cheap_percentile": 30,
            "battery_capacity_kwh": 13.5, "battery_soc_entity": "sensor.soc",
            "battery_charge_power_kw": 0.3, "battery_power_kw": 3.0,
        },
    )
    await setup(hass, entry)

    for domain, key in old:
        assert registry.async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{key}") is None
    assert dict(entry.options) == {"buy_vat_pct": 9.0, "cheap_percentile": 30}  # price settings stay
    # Prices keep working, and the price entities are untouched.
    assert hass.states.get("sensor.opcom_pzu_spot_price").state not in ("unknown", "unavailable")
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_upcoming_prices")


async def test_no_battery_entities_are_created(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    assert not [s for s in hass.states.async_all() if "battery" in s.entity_id]


async def test_options_menu_has_no_battery_page(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, empty=(date(2026, 11, 10),))
    await setup(hass, entry)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["menu_options"] == ["buy", "sell", "general", "finish"]


async def test_the_series_follows_the_price_series_contract(hass, entry, aioclient_mock, freezer):
    """Battery Planner reads this sensor: its shape is a promise (docs/price-series.md)."""
    from datetime import datetime

    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), DAY, date(2026, 11, 10))
    await setup(hass, entry)
    state = hass.states.get("sensor.opcom_pzu_upcoming_prices")
    assert state.attributes["unit"] == "RON/kWh"
    previous_end = None
    for row in state.attributes["prices"]:
        start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        assert start.utcoffset() is not None and end.utcoffset() is not None  # an offset on every time
        assert end - start == timedelta(minutes=15)
        assert isinstance(row["buy"], float) and isinstance(row["sell"], float)
        assert previous_end in (None, start)  # contiguous, oldest first
        previous_end = end
