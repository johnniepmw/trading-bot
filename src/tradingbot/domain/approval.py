"""ApprovedOrder: an order the risk layer has passed (ADR-0016).

The order port accepts only this type, so an order that skipped risk does not
type-check. Three checks keep construction inside `risk`:
- import contract 5 (.importlinter): only risk, execution and brokers import this module;
- `tests/domain/test_approval.py`: outside `risk`, the name may appear only in an
  un-aliased import, an annotation or `isinstance`, so aliases, `partial`, `__new__`
  and subclassing fail the test;
- the class is final, immutable and not a dataclass, so `dataclasses.replace` and
  `copy.replace` raise instead of re-wrapping an order risk never saw.
The scan also flags `type(x)(...)`, `x.__class__` and imports of the name from anywhere but
this module. Accepted gap: `object.__new__`/`object.__setattr__` on an instance, and
string lookups (`getattr`); the order port's runtime `isinstance` check is the backstop.
"""

from __future__ import annotations

from typing import final

from tradingbot.domain.market import Order


@final
class ApprovedOrder:
    __slots__ = ("order",)
    order: Order

    def __init__(self, order: Order) -> None:
        if not isinstance(order, Order):
            raise TypeError(f"ApprovedOrder wraps an Order, got {type(order).__name__}")
        object.__setattr__(self, "order", order)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ApprovedOrder cannot be subclassed")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("ApprovedOrder is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ApprovedOrder is immutable")

    def __copy__(self) -> ApprovedOrder:
        return self  # immutable: a copy is the same approved order

    def __deepcopy__(self, memo: dict[int, object]) -> ApprovedOrder:
        return self

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ApprovedOrder) and other.order == self.order

    def __hash__(self) -> int:
        return hash(self.order)

    def __repr__(self) -> str:
        return f"ApprovedOrder({self.order!r})"
