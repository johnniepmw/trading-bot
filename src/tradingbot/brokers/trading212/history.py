"""Trading 212's order history, paged back far enough for reconcile (Phase 2 step 4).

`recent_orders()` must list every order since the oldest open intent (the port's contract):
working orders from `/equity/orders` (read first), then finished ones from
`/equity/history/orders`, newest first, 50 a page, followed until a page reaches back past
the look-back window. If the
window isn't reached within `MAX_PAGES`, the list can't be known complete: a BrokerError,
so reconcile stops instead of missing an order.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Protocol

from tradingbot.brokers.port import BrokerError, BrokerOrder
from tradingbot.brokers.trading212.orders import Fill, Tickers, parse_fill, parse_order, timestamp, with_fills

API_PREFIX = "/api/v0"
HISTORY = "/equity/history/orders"
PAGE_SIZE = 50
# Covers the oldest open intent: runs act on a month end for at most MAX_SIGNAL_AGE (35 days),
# and a paused schedule or a stale lock can leave sent intents open that long. Pages count
# orders, not days, so a longer window costs about nothing at this bot's volume.
LOOK_BACK = timedelta(days=40)
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
    active = client.get("/equity/orders", "orders")  # first: an order finishing meanwhile then
    if not isinstance(active, list):  # shows up in the history read after it
        raise BrokerError("Trading 212 sent an active-order list that is not a list")
    for raw in active:
        parsed = parse_order(raw, tickers)
        if parsed is not None:
            found[parsed.order_id] = parsed
    fills: dict[str, list[Fill]] = {}
    since = now - LOOK_BACK
    reached = False
    for count, items in enumerate(history_pages(client), start=1):
        for item in items:
            raw = item.get("order") if isinstance(item, dict) else None
            if isinstance(raw, dict) and timestamp(raw, "createdAt") < since:
                reached = True
                continue
            parsed = parse_order(raw, tickers)
            if parsed is None:
                continue
            found[parsed.order_id] = parsed  # finished: newer than any active copy
            fill = parse_fill(item)
            if fill is not None:  # one history item per fill
                fills.setdefault(parsed.order_id, []).append(fill)
        if reached:
            break
        if count == MAX_PAGES:
            raise BrokerError(f"order history goes past {MAX_PAGES} pages within {LOOK_BACK.days} days")
    priced = [with_fills(o, fills.get(o.order_id, [])) for o in found.values()]
    return sorted(priced, key=lambda o: (o.created_at, o.order_id))


def find_order(client: Reader, tickers: Tickers, order_id: str, now: datetime) -> BrokerOrder | None:
    """A finished order by ID, from the history within the look-back window."""
    for order in recent_orders(client, tickers, now):
        if order.order_id == order_id:
            return order
    return None
