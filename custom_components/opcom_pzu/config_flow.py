"""Config and options flow."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)
from homeassistant.util import dt as dt_util

from . import settings
from .api import OpcomClient, OpcomError
from .const import (
    CONF_BUY_FIXED_CHARGES,
    CONF_BUY_FORMULA,
    CONF_BUY_MARKUP,
    CONF_BUY_VAT,
    CONF_CHEAP_PERCENTILE,
    CONF_CURRENCY,
    CONF_HISTORY_MONTHS,
    CONF_SELL_COMMISSION,
    CONF_SELL_FIXED_FEE,
    CONF_SELL_FORMULA,
    CURRENCIES,
    DEFAULT_CHEAP_PERCENTILE,
    DEFAULT_CURRENCY,
    DEFAULT_HISTORY_MONTHS,
    DOMAIN,
    MAX_HISTORY_MONTHS,
    NAME,
)
from .pricing import FormulaError
from .session import create_session


def _num(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


_TEXT = TextSelector()


def _months_selector() -> NumberSelector:
    return _num(0, MAX_HISTORY_MONTHS, 1, "months")


class OpcomPzuConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = create_session(self.hass)
            client = OpcomClient(session, CURRENCIES[user_input[CONF_CURRENCY]])
            try:
                # A published day proves OPCOM is reachable and parseable.
                await client.fetch_day(dt_util.now().date())
            except OpcomError:
                errors["base"] = "cannot_connect"
            finally:
                await session.close()
            if not errors:
                return self.async_create_entry(
                    title=NAME,
                    data={
                        CONF_CURRENCY: user_input[CONF_CURRENCY],
                        CONF_HISTORY_MONTHS: int(user_input[CONF_HISTORY_MONTHS]),
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_CURRENCY, default=DEFAULT_CURRENCY): SelectSelector(
                    SelectSelectorConfig(options=list(CURRENCIES))
                ),
                vol.Required(CONF_HISTORY_MONTHS, default=DEFAULT_HISTORY_MONTHS): _months_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        return OpcomPzuOptionsFlow()


class OpcomPzuOptionsFlow(OptionsFlow):
    """Menu of settings pages. Nothing is saved until 'Save and close'."""

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self._options:
            self._options = dict(self.config_entry.options)
        return self.async_show_menu(
            step_id="init",
            menu_options=["buy", "sell", "general", "finish"],
        )

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self._options)

    # -- helpers -------------------------------------------------------------

    def _preview(self) -> str:
        """What the current settings do to a sample spot price."""
        entry = self.config_entry
        cur = entry.data[CONF_CURRENCY]
        spot = 0.5
        coordinator = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
        if coordinator and coordinator.data:
            now = coordinator.data.series.at(dt_util.utcnow())
            spot = now.pzu if now else spot
        try:
            tariff = settings.tariff(self._options).compile()
            hour = dt_util.now().hour
            return (
                f"At a spot price of {spot:.3f} {cur}/kWh you would pay "
                f"{tariff.buy_price(spot, hour):.3f} {cur}/kWh and receive "
                f"{tariff.sell_price(spot, hour):.3f} {cur}/kWh."
            )
        except (FormulaError, ZeroDivisionError):
            return ""

    def _form(self, step: str, schema: dict, errors: dict[str, str] | None = None,
              placeholders: dict[str, str] | None = None) -> ConfigFlowResult:
        return self.async_show_form(
            step_id=step,
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(schema),
                {k.schema: self._options.get(k.schema, settings.DEFAULTS.get(k.schema)) for k in schema},
            ),
            errors=errors or {},
            description_placeholders={
                "currency": self.config_entry.data[CONF_CURRENCY],
                "preview": self._preview(),
                "error": "",
                **(placeholders or {}),
            },
        )

    async def _tariff_step(self, step: str, schema: dict, formula_key: str,
                           user_input: dict[str, Any] | None) -> ConfigFlowResult:
        if user_input is not None:
            candidate = self._options | user_input
            for key in schema:  # an emptied field is left out of the submission
                if key.schema not in user_input:
                    candidate[key.schema] = "" if key.schema.endswith("_formula") else 0.0
            try:
                settings.tariff(candidate).compile()
            except FormulaError as err:
                self._options = candidate  # keep what they typed
                return self._form(step, schema, {formula_key: "invalid_formula"}, {"error": str(err)})
            self._options = candidate
            return await self.async_step_init()
        return self._form(step, schema)

    # -- pages ---------------------------------------------------------------

    async def async_step_sell(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = {
            vol.Optional(CONF_SELL_COMMISSION): _num(0, 100, 0.1, "%"),
            vol.Optional(CONF_SELL_FIXED_FEE): _num(0, 100, 0.001),
            vol.Optional(CONF_SELL_FORMULA): _TEXT,
        }
        return await self._tariff_step("sell", schema, CONF_SELL_FORMULA, user_input)

    async def async_step_buy(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = {
            vol.Optional(CONF_BUY_MARKUP): _num(0, 1000, 0.1, "%"),
            vol.Optional(CONF_BUY_FIXED_CHARGES): _num(0, 100, 0.001),
            vol.Optional(CONF_BUY_VAT): _num(0, 100, 0.1, "%"),
            vol.Optional(CONF_BUY_FORMULA): _TEXT,
        }
        return await self._tariff_step("buy", schema, CONF_BUY_FORMULA, user_input)

    async def async_step_general(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        schema = {
            vol.Required(CONF_HISTORY_MONTHS): _months_selector(),
            vol.Required(CONF_CHEAP_PERCENTILE): _num(1, 50, 1, "%"),
        }
        if user_input is not None:
            self._options |= {k: int(v) for k, v in user_input.items()}
            return await self.async_step_init()
        self._options.setdefault(
            CONF_HISTORY_MONTHS,
            self.config_entry.data.get(CONF_HISTORY_MONTHS, DEFAULT_HISTORY_MONTHS),
        )
        self._options.setdefault(CONF_CHEAP_PERCENTILE, DEFAULT_CHEAP_PERCENTILE)
        return self._form("general", schema)
