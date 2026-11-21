"""ADR-0016: only the risk layer may create an ApprovedOrder (see domain/approval.py)."""

from __future__ import annotations

import ast
import copy
import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

from tradingbot.domain.approval import ApprovedOrder
from tradingbot.domain.market import Instrument, Order, Side
from tradingbot.domain.money import Currency

SRC = Path(__file__).resolve().parents[2] / "src" / "tradingbot"
NAME = "ApprovedOrder"
ORDER = Order(Instrument("VWRP", Currency.GBP), Side.BUY, Decimal(1))
BIGGER = Order(Instrument("VWRP", Currency.GBP), Side.BUY, Decimal(100))


def _allowed_nodes(tree: ast.AST) -> set[int]:
    """ids of nodes inside annotations or isinstance's second argument."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            all_args = (
                args.posonlyargs + args.args + args.kwonlyargs + [a for a in (args.vararg, args.kwarg) if a]
            )
            roots += [a.annotation for a in all_args if a.annotation is not None]
            if node.returns is not None:
                roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            roots.append(node.args[1])
    return {id(n) for root in roots for n in ast.walk(root)}


def _names_it(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == NAME) or (
        isinstance(node, ast.Attribute) and node.attr == NAME
    )


def _rebuilds_from_an_instance(node: ast.AST) -> bool:
    """type(x)(...) or x.__class__: building a new object from one already held."""
    calls_type_result = (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Call)
        and isinstance(node.func.func, ast.Name)
        and node.func.func.id == "type"
    )
    return calls_type_result or (isinstance(node, ast.Attribute) and node.attr == "__class__")


def misuses(source: str) -> list[int]:
    """Lines that use ApprovedOrder other than in an un-aliased import, annotation or isinstance."""
    tree = ast.parse(source)
    allowed = _allowed_nodes(tree)
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            lines += [
                node.lineno
                for a in node.names
                if a.name == NAME and (a.asname is not None or node.module != "tradingbot.domain.approval")
            ]
        elif _rebuilds_from_an_instance(node) or (_names_it(node) and id(node) not in allowed):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_only_risk_constructs_approved_orders() -> None:
    scanned, offenders = [], []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC)
        scanned.append(rel)
        if rel.parts[0] == "risk" or rel == Path("domain/approval.py"):
            continue
        offenders += [f"{rel}:{line}" for line in misuses(path.read_text())]
    assert Path("domain/approval.py") in scanned  # the scan looked in the right place
    assert offenders == []


ALLOWED = """
from tradingbot.domain.approval import ApprovedOrder
def send(order: ApprovedOrder) -> list[ApprovedOrder]:
    if not isinstance(order, ApprovedOrder):
        raise TypeError
    held: ApprovedOrder = order
    return [held]
"""


def test_the_scan_allows_imports_annotations_and_isinstance() -> None:
    assert misuses(ALLOWED) == []


@pytest.mark.parametrize(
    "planted",
    [
        "ApprovedOrder(o)",
        "x.ApprovedOrder(o)",
        "from tradingbot.domain.approval import ApprovedOrder as Approved",
        "cls = ApprovedOrder",
        "functools.partial(ApprovedOrder)(o)",
        "ApprovedOrder.__new__(ApprovedOrder)",
        "class Mine(ApprovedOrder): pass",
        "approval.ApprovedOrder(o)",
        "from tradingbot.brokers.port import ApprovedOrder",
        "type(approved)(o)",
        "approved.__class__(o)",
    ],
)
def test_the_scan_finds_each_way_round_it(planted: str) -> None:
    assert misuses(planted) != []


def test_an_approved_order_wraps_exactly_one_order() -> None:
    approved = ApprovedOrder(ORDER)
    assert approved.order is ORDER
    assert approved == ApprovedOrder(ORDER)
    assert hash(approved) == hash(ApprovedOrder(ORDER))
    assert approved != ApprovedOrder(BIGGER)
    assert approved != ORDER
    assert "VWRP" in repr(approved)


def test_it_wraps_only_orders() -> None:
    with pytest.raises(TypeError, match="Order"):
        ApprovedOrder("buy everything")  # type: ignore[arg-type]


def test_it_cannot_be_rewrapped_mutated_or_subclassed() -> None:
    approved = ApprovedOrder(ORDER)
    with pytest.raises(TypeError):
        dataclasses.replace(approved, order=BIGGER)  # type: ignore[type-var]
    with pytest.raises(TypeError):
        copy.replace(approved, order=BIGGER)  # type: ignore[type-var]
    with pytest.raises(AttributeError, match="immutable"):
        approved.order = BIGGER  # type: ignore[misc]
    with pytest.raises(AttributeError, match="immutable"):
        del approved.order  # type: ignore[misc]
    with pytest.raises(TypeError, match="subclassed"):
        type("Mine", (ApprovedOrder,), {})
    assert copy.copy(approved) is approved  # copies are the same approved order
    assert copy.deepcopy(approved) is approved
