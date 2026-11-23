#!/usr/bin/env python3
"""Record the SHAPE of Trading 212's demo API responses, for the adapter's tests (Phase 2 step 4).

Prints each response with every number replaced by "<number>" and every free-text string
by "<string>". Only names the adapter must match are kept as they are: status, type, side,
currency codes, tickers, error codes and the like. So the output holds no balances,
quantities, prices or order IDs. With `--order` it also checks the smallest quantity step
(acceptance test A2): it buys a tiny amount of one fund with five decimal places, prints
the quantity Trading 212 recorded and whether the fill price is in pence or pounds (a
verdict, not the price), sells it back, and checks nothing is left: no pending order and no
change in that fund's position. Anything left is a WARNING and exit code 1. Use a fund
priced in pence (SGLN) for the units verdict.

DEMO ACCOUNT, except `--live-transactions`: it reads only the live account's transaction
list (to see how deposits and withdrawals look), refuses any other request, and follows no
redirects or proxies. Run it on your own machine (never in a cloud or agent session) with a
dedicated live key that has read permissions only, and delete that key afterwards.
Otherwise it uses the same demo key and secret as phase0_api_check.py:
    read -rs T212_API_KEY && export T212_API_KEY
    read -rs T212_API_SECRET && export T212_API_SECRET
    python3 scripts/t212_record.py                       # read-only
    python3 scripts/t212_record.py --order SGLNl_EQ      # plus the quantity-step check
                                                         # (London market hours only)
    python3 scripts/t212_record.py --live-transactions   # LIVE key, read-only permissions
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase0_api_check import Client, _base_url

KEEP = frozenset(
    {
        "status",
        "type",
        "side",
        "strategy",
        "currency",
        "currencyCode",
        "tradingMethod",
        "initiatedFrom",
        "timeValidity",
        "timeInForce",
        "ticker",
        "code",
        "nextPagePath",
    }
)
# Tried in turn until one is accepted: the first accepted shows the finest quantity step.
STEP_QUANTITIES = (0.12345, 0.1234, 0.123, 0.12, 0.1)
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+(Z|[+-][0-9:]+)?")
PENDING = {"LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "PARTIALLY_FILLED", "REPLACING"}


def shape(value: object, key: str = "") -> object:  # noqa: PLR0911 - one return per JSON type
    """The response with its values hidden, except the names the adapter must match."""
    if isinstance(value, dict):
        return {k: shape(v, k) for k, v in value.items()}
    if isinstance(value, list):
        merged: dict[str, object] = {}
        for item in value:
            if isinstance(item, dict):
                merged.update(shape(item))  # type: ignore[arg-type]
        if merged:
            return [merged, f"<{len(value)} item(s)>"]
        return [shape(item, key) for item in value[:3]]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return "<number>"
    if isinstance(value, str):
        if key == "nextPagePath":
            return value.split("?")[0] + ("?<query>" if "?" in value else "")
        if key in KEEP:
            return value
        if _TIMESTAMP.fullmatch(value):
            return re.sub(r"[0-9]", "9", value)  # the format, not the time
        return "<string>"
    return f"<{type(value).__name__}>"


def show(title: str, res: dict) -> None:
    print(f"\n=== {title}: HTTP {res['status']}")
    print(json.dumps(shape(res["data"]), indent=1))


def read_only(client: Client) -> None:
    show("GET /equity/account/summary", client.call("GET", "/equity/account/summary", "summary"))
    show("GET /equity/positions", client.call("GET", "/equity/positions", "positions"))
    show("GET /equity/orders", client.call("GET", "/equity/orders", "orders_list"))
    show(
        "GET /equity/history/orders?limit=5", client.call("GET", "/equity/history/orders?limit=5", "history")
    )
    res = client.call("GET", "/history/transactions?limit=50", "history")
    show("GET /history/transactions?limit=50", res)
    print("Transaction kinds (type, sign of amount, count):")
    for line in kinds(res["data"]):
        print(f"  {line}")


def kinds(page: object) -> list[str]:
    """Each transaction type with the sign of its amounts and a count: no amounts or dates."""
    items = page.get("items", []) if isinstance(page, dict) else []
    counts: dict[tuple[str, str], int] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        sign = (
            "?" if not isinstance(amount, int | float) else "+" if amount > 0 else "-" if amount < 0 else "0"
        )
        key = (str(item.get("type")), sign)
        counts[key] = counts.get(key, 0) + 1
    return [f"{kind} {sign} x{n}" for (kind, sign), n in sorted(counts.items())] or ["(none)"]


def _held(client: Client, ticker: str) -> float | None:
    """This fund's quantity now (0 when not held); None when positions can't be read."""
    res = client.call("GET", "/equity/positions", "positions")
    if res["status"] != 200 or not isinstance(res["data"], list):
        return None
    for position in res["data"]:
        if isinstance(position, dict) and ticker in (
            position.get("ticker"),
            (position.get("instrument") or {}).get("ticker"),
        ):
            return float(position.get("quantity") or 0)
    return 0.0


def _settle(client: Client, oid: object) -> tuple[str | None, dict | None]:
    """Wait for an order to finish; its final status and history item (or order)."""
    status, found = None, None
    for _ in range(15):
        res = client.call("GET", f"/equity/orders/{oid}", "order_get")
        if res["status"] == 200 and isinstance(res["data"], dict):
            status, found = res["data"].get("status"), res["data"]
        else:
            hist = client.call("GET", "/equity/history/orders?limit=50", "history")
            items = (hist["data"] or {}).get("items", []) if isinstance(hist["data"], dict) else []
            for item in items:  # flat items too, as phase0_api_check accepts
                order = item.get("order", item) if isinstance(item, dict) else {}
                if order.get("id") == oid:
                    status, found = order.get("status"), item
                    break
        if status is not None and status not in PENDING:
            break
        if not client.fast:
            time.sleep(2)
    return status, found


def _units(item: dict | None, filled: float) -> str:
    """Pence or pounds, judged from the fill's value against quantity x price (no numbers)."""
    fill = (item or {}).get("fill") or {}
    price, value = fill.get("price"), (fill.get("walletImpact") or {}).get("netValue")
    if not isinstance(price, int | float) or not isinstance(value, int | float) or not price or not filled:
        return "unknown (no fill details in the history item)"
    ratio = abs(value) / (abs(filled) * price)
    if 0.9 < ratio < 1.1:
        return "the fill price is in the account currency (pounds)"
    if 0.009 < ratio < 0.011:
        return "the fill price is in pence (1/100 of the account currency)"
    return "neither pounds nor pence: tell the agent"


def _created_pending(client: Client, created: list) -> list:
    res = client.call("GET", "/equity/orders", "orders_list")
    pending = res["data"] if isinstance(res["data"], list) else None
    if pending is None:
        return list(created)  # can't tell: assume the worst
    return [o.get("id") for o in pending if isinstance(o, dict) and o.get("id") in created]


def quantity_step(client: Client, ticker: str) -> bool:
    """Buy the finest accepted quantity, sell it back, and confirm nothing is left. True when clean."""
    start = _held(client, ticker)
    if start is None:
        print("\nCan't read positions; skipping the order check")
        return False
    created: list = []
    try:
        _buy_and_sell(client, ticker, created)
    finally:
        for oid in _created_pending(client, created):
            client.call("DELETE", f"/equity/orders/{oid}", "cancel")
        left = _created_pending(client, created)
        end = _held(client, ticker)
    unchanged = end is not None and abs(end - start) < 1e-9
    clean = not left and unchanged
    print(f"\nNo pending order from this run: {'yes' if not left else 'NO'}")
    print(f"{ticker} position unchanged since the start: {'yes' if unchanged else 'NO (or unreadable)'}")
    if not clean:
        print(f"WARNING: check the demo app; close any {ticker} position or order this run left")
    return clean


def _buy_and_sell(client: Client, ticker: str, created: list) -> None:
    for quantity in STEP_QUANTITIES:
        body = {"ticker": ticker, "quantity": quantity, "extendedHours": False}
        res = client.call("POST", "/equity/orders/market", "market", body)
        data = res["data"] if isinstance(res["data"], dict) else {}
        if res["status"] == 200 and "id" in data:
            break
        if "precision" not in str(data.get("type", "")):
            show(f"POST /equity/orders/market (buy {quantity})", res)
            print(f"\nQuantity step: {quantity} was refused for another reason (see above)")
            return
        print(f"Quantity step: {quantity} refused (precision)")
    else:
        print("\nQuantity step: every quantity was refused for precision")
        return
    show(f"POST /equity/orders/market (buy {quantity})", res)
    oid = data["id"]
    created.append(oid)
    print(f"\nQuantity step: asked for {quantity}, the order records {data.get('quantity')}")
    show(
        "GET /equity/orders/{id} right after placing",
        client.call("GET", f"/equity/orders/{oid}", "order_get"),
    )
    status, item = _settle(client, oid)
    show("the order once finished (history item)", {"status": 200, "data": item})
    if status != "FILLED":
        print(f"Not filled ({status or 'still pending'}; market closed?): it is cancelled below")
        return
    order = (item or {}).get("order", item) or {}
    filled = order.get("filledQuantity") or order.get("quantity")
    print(f"Quantity step: filled {filled}")
    print(f"Units: {_units(item, filled)}")
    show("GET /equity/positions while holding it", client.call("GET", "/equity/positions", "positions"))
    sell = {"ticker": ticker, "quantity": -abs(filled), "extendedHours": False}
    sres = client.call("POST", "/equity/orders/market", "market", sell)
    if sres["status"] == 200 and isinstance(sres["data"], dict) and "id" in sres["data"]:
        created.append(sres["data"]["id"])
        sold, _ = _settle(client, sres["data"]["id"])
        print(f"Sold it back: {sold}")
    else:
        print(f"Sell refused: HTTP {sres['status']}")


LIVE_BASE = "https://live.trading212.com/api/v0"
LIVE_PATH = "/history/transactions"
LIVE_PAGES = 5


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object, **__: object) -> None:
        return None  # a 3xx becomes an HTTP error: the live key never follows a redirect


def lock_down_http() -> None:
    """Live mode: no redirects and no proxies for every request this process makes."""
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirects())
    )


class TransactionsOnly:
    """The live account, read through one door: GET /history/transactions and nothing else.
    Any other method or path ends the script before a request is made (ADR-0009: no orders
    on live; this mode exists only to record how deposits and withdrawals look)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def call(self, method: str, path: str, bucket: str, body: dict | None = None) -> dict:
        if method != "GET" or body is not None or not path.startswith(LIVE_PATH + "?"):
            sys.exit(
                f"Refusing {method} {path.split('?', maxsplit=1)[0]} on the live account: transactions only"
            )
        return self._client.call(method, path, bucket)


def reference_kind(value: object) -> str:
    """What a reference looks like, never the reference itself."""
    if not isinstance(value, str) or not value:
        return "missing"
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value):
        return "uuid"
    if value.isdigit():
        return f"digits({len(value)})"
    return f"text({len(value)})"


def live_transactions(client: TransactionsOnly) -> bool:
    """Every transaction kind on the live account (up to LIVE_PAGES pages), with the shape of
    one page, and what references look like: no amounts, dates or references. True when a
    page couldn't be read."""
    path: str | None = f"{LIVE_PATH}?limit=50"
    items: list[object] = []
    failed = False
    for page_number in range(1, LIVE_PAGES + 1):
        res = client.call("GET", path or "", "history")
        failed = failed or res["status"] != 200
        if page_number == 1:
            show(f"GET {LIVE_PATH}?limit=50 (live)", res)
        else:
            print(f"page {page_number}: HTTP {res['status']}")
        data = res["data"] if isinstance(res["data"], dict) else {}
        items += data.get("items", []) if isinstance(data.get("items"), list) else []
        following = data.get("nextPagePath")
        path = following.removeprefix("/api/v0") if isinstance(following, str) else None
        if path is None:
            break
    else:
        print(f"(stopped after {LIVE_PAGES} pages: older transactions not read)")
    print("Transaction kinds (type, sign of amount, count):")
    for line in kinds({"items": items}):
        print(f"  {line}")
    refs = [i.get("reference") for i in items if isinstance(i, dict)]
    shapes: dict[str, int] = {}
    for ref in refs:
        shapes[reference_kind(ref)] = shapes.get(reference_kind(ref), 0) + 1
    print(f"References: {', '.join(f'{k} x{n}' for k, n in sorted(shapes.items())) or '(none)'}")
    present = [r for r in refs if isinstance(r, str) and r]
    print(f"References unique: {'yes' if len(set(present)) == len(present) else 'NO'}")
    return failed


def _live_base() -> str:
    """Live, or the localhost test server for offline tests only (both test variables set)."""
    if os.environ.get("T212_TEST_BASE_URL") and not os.environ.get("T212_TEST_FAST"):
        sys.exit("T212_TEST_BASE_URL is set in this shell: unset it before reading the live account")
    return _base_url() if os.environ.get("T212_TEST_BASE_URL") else LIVE_BASE


def main() -> int:
    ap = argparse.ArgumentParser(description="Record Trading 212 response shapes (no values)")
    ap.add_argument("--order", metavar="TICKER", help="also check the quantity step with a tiny buy and sell")
    ap.add_argument(
        "--live-transactions",
        action="store_true",
        help="LIVE account, read-only: list transaction kinds (use a key with read permissions only)",
    )
    args = ap.parse_args()
    if args.live_transactions and args.order:
        sys.exit("--order is demo only")
    key, secret = os.environ.get("T212_API_KEY"), os.environ.get("T212_API_SECRET")
    if not key or not secret:
        sys.exit("Set T212_API_KEY and T212_API_SECRET in your shell first.")
    fast = bool(os.environ.get("T212_TEST_BASE_URL")) and bool(os.environ.get("T212_TEST_FAST"))
    if args.live_transactions:
        target = _live_base()
        lock_down_http()
        live = TransactionsOnly(Client(key, secret, target, fast=fast))
        print(f"Reading transaction kinds from {target} (read-only; transactions only)")
        return 1 if live_transactions(live) else 0
    base = _base_url()
    client = Client(key, secret, base, fast=fast)
    print(f"Recording response shapes from {'demo' if 'demo.' in base else 'a local test server'}")
    read_only(client)
    if args.order:
        return 0 if quantity_step(client, args.order) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
