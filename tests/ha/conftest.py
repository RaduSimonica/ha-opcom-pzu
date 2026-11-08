"""Shared fixtures."""

from datetime import date
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.opcom_pzu.const import CONF_CURRENCY, CONF_HISTORY_MONTHS, DOMAIN

FIX = Path(__file__).parent.parent / "fixtures"
CSV = (FIX / "ro_09-11-2026.csv").read_text(encoding="utf-8")


def day_url(day: date, lang: str = "ro") -> str:
    return (
        "https://www.opcom.ro/rapoarte-pzu-raportPIP-export-csv/"
        f"{day:%d}/{day:%m}/{day:%Y}/{lang}?resolution=15"
    )


@pytest.fixture(autouse=True)
async def _setup(recorder_mock, enable_custom_integrations, hass: HomeAssistant):
    """Recorder first (it must exist before hass); Romanian time zone."""
    await hass.config.async_set_time_zone("Europe/Bucharest")


@pytest.fixture
async def entry(hass: HomeAssistant):
    e = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CURRENCY: "RON", CONF_HISTORY_MONTHS: 0},
        options={},
    )
    e.add_to_hass(hass)
    yield e
    if e.state.recoverable or e.state.value == "loaded":
        await hass.config_entries.async_unload(e.entry_id)
        await hass.async_block_till_done()


def publish(aioclient_mock, *days: date, empty: tuple[date, ...] = ()) -> None:
    for d in days:
        aioclient_mock.get(day_url(d), text=CSV)
    for d in empty:
        aioclient_mock.get(day_url(d), text="")
