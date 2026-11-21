"""ApprovedOrder: an order the risk layer has passed (ADR-0016).

The order port accepts only this type, so an order that skipped risk does not
type-check. Who may import this module is limited by an import contract (risk,
execution, brokers), and `tests/domain/test_approval.py` checks that only `risk`
constructs one.
"""

from __future__ import annotations

from dataclasses import dataclass

from tradingbot.domain.market import Order


@dataclass(frozen=True, slots=True)
class ApprovedOrder:
    order: Order
