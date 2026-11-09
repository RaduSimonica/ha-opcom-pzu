"""Turn the raw PZU price into what the user actually pays or receives.

All prices here are per kWh. Two modes exist for both directions:

* simple fields (commission, fixed fees, VAT), covered by most contracts;
* an advanced formula, a restricted arithmetic expression over ``pzu`` (the
  spot price) and ``hour`` (local hour of the interval).
"""

from __future__ import annotations

import ast
import logging
import math
import operator
from dataclasses import dataclass
from typing import Any, Callable

_BIN_OPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UNARY_OPS: dict[type, Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS: dict[str, Callable[..., float]] = {
    "min": min,
    "max": max,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
_LOGGER = logging.getLogger(__name__)
VARIABLES = ("pzu", "hour")


class FormulaError(ValueError):
    """The formula is invalid."""


class FormulaMathError(FormulaError):
    """The formula is valid but cannot be computed for this price (e.g. 1/0)."""


def _eval(node: ast.AST, env: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise FormulaError(f"Unknown variable '{node.id}' (use: {', '.join(VARIABLES)})")
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand, env))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        return _FUNCS[node.func.id](*(_eval(a, env) for a in node.args))
    raise FormulaError("Only numbers, + - * / ** %, pzu, hour, min, max, abs, round, floor, ceil are allowed")


class Formula:
    """A validated formula, compiled once and evaluated per interval."""

    def __init__(self, expression: str) -> None:
        self.expression = expression.strip()
        try:
            self._tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as err:
            raise FormulaError(f"Syntax error: {err.msg}") from err
        # Dry run so every error surfaces when the user saves the form.
        try:
            self._compute(0.5, 12)
        except ZeroDivisionError:
            pass
        except (TypeError, OverflowError, ValueError) as err:
            raise FormulaError(str(err)) from err

    def _compute(self, pzu: float, hour: int) -> float:
        return float(_eval(self._tree, {"pzu": pzu, "hour": float(hour)}))

    def __call__(self, pzu: float, hour: int) -> float:
        """Prices can be zero or negative, where some formulas have no answer."""
        try:
            return self._compute(pzu, hour)
        except (ZeroDivisionError, OverflowError, TypeError, ValueError) as err:
            raise FormulaMathError(
                f"'{self.expression}' cannot be computed for pzu={pzu}: {err}"
            ) from err


@dataclass(frozen=True, slots=True)
class Tariff:
    """Buy and sell price rules, per kWh, in the configured currency."""

    # Selling (export): you receive pzu minus the supplier's cut.
    sell_commission_pct: float = 0.0
    sell_fixed_fee: float = 0.0
    sell_formula: str = ""
    # Buying (import): you pay (pzu*(1+markup%) + fixed charges) * (1+VAT%).
    buy_markup_pct: float = 0.0
    buy_fixed_charges: float = 0.0
    buy_vat_pct: float = 0.0
    buy_formula: str = ""

    def compile(self) -> "CompiledTariff":
        return CompiledTariff(
            self,
            Formula(self.sell_formula) if self.sell_formula.strip() else None,
            Formula(self.buy_formula) if self.buy_formula.strip() else None,
        )


class CompiledTariff:
    def __init__(self, tariff: Tariff, sell: Formula | None, buy: Formula | None) -> None:
        self.tariff = tariff
        self._sell = sell
        self._buy = buy
        self._warned: set[str] = set()

    def _guard(self, formula: Formula, pzu: float, hour: int, fallback: float) -> float:
        try:
            return formula(pzu, hour)
        except FormulaMathError as err:
            if formula.expression not in self._warned:
                self._warned.add(formula.expression)
                _LOGGER.warning("%s; using the simple fields for this price instead", err)
            return fallback

    def sell_price(self, pzu: float, hour: int = 0) -> float:
        t = self.tariff
        simple = pzu * (1 - t.sell_commission_pct / 100) - t.sell_fixed_fee
        if self._sell:
            return self._guard(self._sell, pzu, hour, simple)
        return simple

    def buy_price(self, pzu: float, hour: int = 0) -> float:
        t = self.tariff
        simple = (pzu * (1 + t.buy_markup_pct / 100) + t.buy_fixed_charges) * (1 + t.buy_vat_pct / 100)
        if self._buy:
            return self._guard(self._buy, pzu, hour, simple)
        return simple

    def describe(self) -> dict[str, Any]:
        return {"sell": self._sell.expression if self._sell else "pzu*(1-commission)-fee",
                "buy": self._buy.expression if self._buy else "(pzu*(1+markup)+charges)*(1+VAT)"}
