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


async def test_emptied_fields_are_reset(hass, entry, aioclient_mock, freezer):
    """The frontend leaves an emptied optional field out of the submission."""
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9))
    hass.config_entries.async_update_entry(
        entry, options={"sell_formula": "pzu*0.9", "sell_fixed_fee": 0.2}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "sell"})
    ok = await hass.config_entries.options.async_configure(flow["flow_id"], {"sell_commission_pct": 5})
    done = await hass.config_entries.options.async_configure(ok["flow_id"], {"next_step_id": "finish"})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["sell_formula"] == "" and entry.options["sell_fixed_fee"] == 0.0
    assert entry.options["sell_commission_pct"] == 5


async def _open(hass, entry, page):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": page})


async def test_picking_a_plan_fills_the_formula(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10))
    flow = await _open(hass, entry, "buy")
    shown = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"buy_plan": "eon_dinamic_pro"}
    )
    # The page is shown again with the formula filled, so it can be checked.
    assert shown["type"] is FlowResultType.FORM and shown["step_id"] == "buy"
    assert any(
        k.schema == "buy_formula" and k.description["suggested_value"] == "(pzu + 0.10 + 0.464) * 1.21"
        for k in shown["data_schema"].schema
    )
    ok = await hass.config_entries.options.async_configure(
        shown["flow_id"], {"buy_plan": "eon_dinamic_pro", "buy_formula": "(pzu + 0.10 + 0.464) * 1.21"}
    )
    done = await hass.config_entries.options.async_configure(ok["flow_id"], {"next_step_id": "finish"})
    assert done["data"]["buy_plan"] == "eon_dinamic_pro"
    assert done["data"]["buy_formula"] == "(pzu + 0.10 + 0.464) * 1.21"


async def test_editing_a_plan_formula_makes_it_custom(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10))
    hass.config_entries.async_update_entry(
        entry, options={"sell_plan": "eon_dinamic_pro", "sell_formula": "pzu"}
    )
    flow = await _open(hass, entry, "sell")
    ok = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sell_plan": "eon_dinamic_pro", "sell_formula": "pzu * 0.9"}
    )
    done = await hass.config_entries.options.async_configure(ok["flow_id"], {"next_step_id": "finish"})
    assert done["data"]["sell_plan"] == "custom" and done["data"]["sell_formula"] == "pzu * 0.9"


async def test_choosing_custom_keeps_what_was_typed(hass, entry, aioclient_mock, freezer):
    freezer.move_to("2026-11-09 12:00:00+02:00")
    publish(aioclient_mock, date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10))
    hass.config_entries.async_update_entry(
        entry, options={"sell_plan": "yellowgrid", "sell_formula": "max(0.125 + 0.75*pzu, 0.5)"}
    )
    flow = await _open(hass, entry, "sell")
    ok = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"sell_plan": "custom", "sell_formula": "pzu - 0.1"}
    )
    done = await hass.config_entries.options.async_configure(ok["flow_id"], {"next_step_id": "finish"})
    assert done["data"]["sell_plan"] == "custom" and done["data"]["sell_formula"] == "pzu - 0.1"
