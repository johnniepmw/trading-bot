"""Trading 212's order history, paged back far enough for reconcile (Phase 2 step 4).

`recent_orders()` must list every order since the oldest open intent (the port's contract):
working orders from `/equity/orders`, finished ones from `/equity/history/orders`, newest
first, 50 a page, followed until a page reaches back past the look-back window. If the
window isn't reached within `MAX_PAGES`, the list can't be known complete: a BrokerError,
so reconcile stops instead of missing an order.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Protocol

from tradingbot.brokers.port import BrokerError, BrokerOrder
from tradingbot.brokers.trading212.orders import Tickers, parse_order, timestamp

API_PREFIX = "/api/v0"
HISTORY = "/equity/history/orders"
PAGE_SIZE = 50
LOOK_BACK = timedelta(days=8)  # the port promises at least 7 days
MAX_PAGES = 10


class Reader(Protocol):
    def get(self, path: str, group: str) -> object: ...


def _items(page: object) -> tuple[list[object], str | None]:
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise BrokerError("Trading 212 sent an order history page without items")
    following = page.get("nextPagePath")
    if following is None:
        return page["items"], None
    if not isinstance(following, str) or not following.startswith(f"{API_PREFIX}{HISTORY}?"):
        raise BrokerError("Trading 212 sent an unexpected next page link")
    return page["items"], following.removeprefix(API_PREFIX)


def history_pages(client: Reader) -> Iterator[list[object]]:
    path: str | None = f"{HISTORY}?limit={PAGE_SIZE}"
    while path is not None:
        items, path = _items(client.get(path, "history"))
        yield items


def recent_orders(client: Reader, tickers: Tickers, now: datetime) -> list[BrokerOrder]:
    """Every order of ours since `now - LOOK_BACK`, working ones included, oldest first."""
    found: dict[str, BrokerOrder] = {}
    since = now - LOOK_BACK
    reached = False
    for count, items in enumerate(history_pages(client), start=1):
        for item in items:
            raw = item.get("order") if isinstance(item, dict) else None
            if isinstance(raw, dict) and timestamp(raw, "createdAt") < since:
                reached = True
                continue
            parsed = parse_order(raw, tickers)
            if parsed is not None:
                found[parsed.order_id] = parsed
        if reached:
            break
        if count == MAX_PAGES:
            raise BrokerError(f"order history goes past {MAX_PAGES} pages within {LOOK_BACK.days} days")
    active = client.get("/equity/orders", "orders")
    if not isinstance(active, list):
        raise BrokerError("Trading 212 sent an active-order list that is not a list")
    for raw in active:
        parsed = parse_order(raw, tickers)
        if parsed is not None:
            found[parsed.order_id] = parsed  # the live state wins over a history copy
    return sorted(found.values(), key=lambda o: (o.created_at, o.order_id))


def find_order(client: Reader, tickers: Tickers, order_id: str, now: datetime) -> BrokerOrder | None:
    """A finished order by ID, from the history within the look-back window."""
    for order in recent_orders(client, tickers, now):
        if order.order_id == order_id:
            return order
    return None
