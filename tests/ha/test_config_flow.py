"""Config and options flow."""

from datetime import date

from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.opcom_pzu.const import DOMAIN

from .conftest import day_url, publish


async def test_user_flow(hass, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 9))
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"currency": "RON", "history_months": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"currency": "RON", "history_months": 0}


async def test_user_flow_cannot_connect(hass, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    aioclient_mock.get(day_url(date(2026, 11, 9)), status=500)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"currency": "RON", "history_months": 0}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_validates_formula(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.MENU
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "sell"})
    bad = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sell_formula": "__import__('os')"}
    )
    assert bad["errors"] == {"sell_formula": "invalid_formula"}

    ok = await hass.config_entries.options.async_configure(
        bad["flow_id"], {"sell_commission_pct": 10, "sell_formula": "pzu*0.9"}
    )
    assert ok["type"] is FlowResultType.MENU  # back to the menu, nothing saved yet
    assert not entry.options
    done = await hass.config_entries.options.async_configure(ok["flow_id"], {"next_step_id": "finish"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # the reload triggered by new options
    assert entry.options["sell_formula"] == "pzu*0.9"
