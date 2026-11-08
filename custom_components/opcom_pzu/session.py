"""HTTP session for OPCOM."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession


def create_session(hass: HomeAssistant) -> aiohttp.ClientSession:
    """A session that never keeps cookies.

    opcom.ro hands out XSRF-TOKEN cookies, and answers HTTP 403 once a session
    replays them for a while (after roughly 50 requests). The public CSV export
    needs no cookies, so Home Assistant's shared session, which keeps them, must
    not be used.
    """
    return async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
