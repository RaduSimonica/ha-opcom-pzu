"""Formulas for common Romanian supplier plans.

Prices are per kWh in RON, VAT included on the buy price. Suppliers change their
rates: the figures are as published on SUPPLIER_FIGURES_DATE, and the user can
edit the formula after picking a plan.
"""

from __future__ import annotations

from typing import NamedTuple

CUSTOM = "custom"
SUPPLIER_FIGURES_DATE = "2026-11-09"


class Plan(NamedTuple):
    label: str
    buy: str
    sell: str


PLANS: dict[str, Plan] = {
    "eon_dinamic_pro": Plan(
        "E.ON Dinamic Pro",
        "(pzu + 0.10 + 0.464) * 1.21",
        "pzu",
    ),
    "ppc_maxcontrol": Plan(
        "PPC MaxControl",
        "(max(min(pzu, 0.999), 0) + 0.097 + 0.00363 + 0.464) * 1.21",
        "max(pzu, 0)",
    ),
    "yellowgrid": Plan(
        "Yellowgrid",
        "(min(0.75*pzu + 0.1375, 0.55) + 0.464) * 1.21",
        "max(0.125 + 0.75*pzu, 0.5)",
    ),
}


def options() -> list[dict[str, str]]:
    """Choices for the plan selector ("buy" or "sell" page)."""
    return [{"value": CUSTOM, "label": "Custom"}] + [
        {"value": key, "label": plan.label} for key, plan in PLANS.items()
    ]


def formula(key: str, side: str) -> str:
    return getattr(PLANS[key], side)
