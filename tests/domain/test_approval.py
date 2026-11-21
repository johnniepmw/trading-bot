"""ADR-0016: only the risk layer may create an ApprovedOrder.

The import contract in .importlinter limits who may import `tradingbot.domain.approval`
(risk, execution, brokers). This test closes the remaining gap: of those, only `risk`
may *construct* one.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency

SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot"


def constructor_calls(tree: ast.AST) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "ApprovedOrder")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "ApprovedOrder")
        )
    ]


def test_only_risk_constructs_approved_orders() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        if rel.parts[0] == "risk":
            continue
        offenders += [f"{rel}:{line}" for line in constructor_calls(ast.parse(path.read_text()))]
    assert offenders == []


def test_the_scan_finds_a_planted_constructor_call() -> None:
    planted = (
        "from tradingbot.domain.approval import ApprovedOrder\nApprovedOrder(order)\nx.ApprovedOrder(o)\n"
    )
    assert constructor_calls(ast.parse(planted)) == [2, 3]


def test_an_approved_order_wraps_exactly_one_order() -> None:
    order = Order(Instrument("VWRP", Currency.GBP), Side.BUY, Decimal(1))
    assert ApprovedOrder(order).order is order
