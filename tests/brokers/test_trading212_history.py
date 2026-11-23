"""Paging Trading 212's order history for reconcile (made-up values; shapes from the demo)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingbot.brokers.port import BrokerError, OrderStatus
from tradingbot.brokers.trading212.history import MAX_PAGES, find_order, recent_orders
from tradingbot.brokers.trading212.orders import Tickers
from tradingbot.domain.market import Instrument
from tradingbot.domain.money import Currency

VWRP = Instrument("VWRP", Currency.GBP)
TICKERS = Tickers(frozenset({VWRP}))
NOW = datetime(2026, 11, 23, 7, 15, tzinfo=UTC)


def raw_order(
    order_id: int, days_ago: float, status: str = "FILLED", ticker: str = "VWRPl_EQ"
) -> dict[str, object]:
    created = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {
        "id": order_id,
        "type": "MARKET",
        "ticker": ticker,
        "quantity": Decimal("0.5"),
        "filledQuantity": Decimal("0.5") if status == "FILLED" else Decimal(0),
        "status": status,
        "side": "BUY",
        "createdAt": created,
    }


class Pages:
    def __init__(self, pages: dict[str, object], active: object = ()) -> None:
        self.pages = pages
        self.active = list(active) if isinstance(active, tuple) else active
        self.asked: list[tuple[str, str]] = []

    def get(self, path: str, group: str) -> object:
        self.asked.append((path, group))
        return self.active if path == "/equity/orders" else self.pages[path]


FIRST = "/equity/history/orders?limit=50"


def page(*orders: dict[str, object], following: str | None = None) -> dict[str, object]:
    return {"items": [{"order": o} for o in orders], "nextPagePath": following}


def test_one_page_reaching_past_the_window_is_enough() -> None:
    pages = Pages({FIRST: page(raw_order(2, 1), raw_order(1, 30))})
    assert [o.order_id for o in recent_orders(pages, TICKERS, NOW)] == ["2"]
    assert pages.asked == [(FIRST, "history"), ("/equity/orders", "orders")]


def test_pages_are_followed_until_the_window_is_reached() -> None:
    second = "/equity/history/orders?cursor=abc&limit=50"
    pages = Pages(
        {
            FIRST: page(raw_order(3, 1), following="/api/v0" + second),
            second: page(raw_order(2, 2), raw_order(1, 9)),
        }
    )
    assert [o.order_id for o in recent_orders(pages, TICKERS, NOW)] == ["2", "3"]


def test_the_last_page_ends_the_list() -> None:
    pages = Pages({FIRST: page(raw_order(1, 1))})
    assert [o.order_id for o in recent_orders(pages, TICKERS, NOW)] == ["1"]


def test_too_many_pages_inside_the_window_is_an_error() -> None:
    paths = [FIRST] + [f"/equity/history/orders?cursor={n}&limit=50" for n in range(1, MAX_PAGES + 1)]
    pages = Pages(
        {paths[n]: page(raw_order(n, 1), following="/api/v0" + paths[n + 1]) for n in range(MAX_PAGES)}
    )
    with pytest.raises(BrokerError, match="pages"):
        recent_orders(pages, TICKERS, NOW)


def test_working_orders_come_from_the_active_list_and_win() -> None:
    pages = Pages(
        {FIRST: page(raw_order(5, 0.1, status="CANCELLED"), raw_order(1, 30))},
        active=(raw_order(5, 0.1, status="NEW"),),
    )
    (only,) = recent_orders(pages, TICKERS, NOW)
    assert only.status is OrderStatus.PENDING


def test_other_instruments_are_left_out() -> None:
    pages = Pages({FIRST: page(raw_order(1, 1, ticker="AAPL_US_EQ"))})
    assert recent_orders(pages, TICKERS, NOW) == []


def test_orders_come_oldest_first() -> None:
    pages = Pages({FIRST: page(raw_order(9, 1), raw_order(8, 3))})
    assert [o.order_id for o in recent_orders(pages, TICKERS, NOW)] == ["8", "9"]


@pytest.mark.parametrize("bad", [[], {"items": "x"}, {"nextPagePath": None}])
def test_a_page_without_items_is_an_error(bad: object) -> None:
    with pytest.raises(BrokerError, match="without items"):
        recent_orders(Pages({FIRST: bad}), TICKERS, NOW)


@pytest.mark.parametrize(
    "link", ["https://evil.example/api/v0/equity/history/orders?x", "/api/v0/equity/orders", 5]
)
def test_an_unexpected_next_page_link_is_refused(link: object) -> None:
    with pytest.raises(BrokerError, match="next page"):
        recent_orders(Pages({FIRST: {"items": [], "nextPagePath": link}}), TICKERS, NOW)


def test_an_active_list_that_is_not_a_list_is_an_error() -> None:
    with pytest.raises(BrokerError, match="not a list"):
        recent_orders(Pages({FIRST: page()}, active={"x": 1}), TICKERS, NOW)


def test_find_order_looks_in_the_history() -> None:
    pages = Pages({FIRST: page(raw_order(7, 1), raw_order(1, 30))})
    found = find_order(pages, TICKERS, "7", NOW)
    assert found is not None and found.status is OrderStatus.FILLED
    assert find_order(pages, TICKERS, "8", NOW) is None
