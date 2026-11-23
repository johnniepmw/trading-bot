#!/usr/bin/env python3
"""Record the SHAPE of Trading 212's demo API responses, for the adapter's tests (Phase 2 step 4).

Prints each response with every number replaced by "<number>" and every free-text string
by "<string>". Only names the adapter must match are kept as they are: status, type, side,
currency codes, tickers, error codes and the like. So the output holds no balances,
quantities, prices or order IDs. With `--order` it also checks the smallest quantity step
(acceptance test A2): it buys a tiny amount of one fund with five decimal places, prints
the quantity Trading 212 recorded, the fill price, the position's current price and the
order's value in pounds (all about this one test order and the fund's public price, not
the account), then sells it back. Use a fund priced in pence (SGLN) to see whether prices
come back in pence or pounds.

DEMO ACCOUNT ONLY. Uses the same key and secret as phase0_api_check.py:
    read -rs T212_API_KEY && export T212_API_KEY
    read -rs T212_API_SECRET && export T212_API_SECRET
    python3 scripts/t212_record.py                       # read-only
    python3 scripts/t212_record.py --order SGLNl_EQ      # plus the quantity-step check
                                                         # (London market hours only)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
        "ticker",
        "code",
        "nextPagePath",
    }
)
STEP_QUANTITY = 0.12345  # five decimals: shows whether the step is 0.0001 or finer
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
        return value if key in KEEP else "<string>"
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
    for path in ("/history/transactions?limit=5", "/equity/history/transactions?limit=5"):
        res = client.call("GET", path, "history")
        show(f"GET {path}", res)
        if res["status"] == 200:
            break


def quantity_step(client: Client, ticker: str) -> None:
    body = {"ticker": ticker, "quantity": STEP_QUANTITY, "extendedHours": False}
    res = client.call("POST", "/equity/orders/market", "market", body)
    show(f"POST /equity/orders/market (buy {STEP_QUANTITY})", res)
    data = res["data"] if isinstance(res["data"], dict) else {}
    if res["status"] != 200 or "id" not in data:
        print(f"\nQuantity step: {STEP_QUANTITY} was refused (see above); try again without --order")
        return
    oid = data["id"]
    print(f"\nQuantity step: asked for {STEP_QUANTITY}, the order records {data.get('quantity')}")
    show(
        "GET /equity/orders/{id} right after placing",
        client.call("GET", f"/equity/orders/{oid}", "order_get"),
    )
    filled = None
    for _ in range(10):
        hist = client.call("GET", "/equity/history/orders?limit=20", "history")
        items = (hist["data"] or {}).get("items", []) if isinstance(hist["data"], dict) else []
        mine = [i for i in items if isinstance(i, dict) and (i.get("order") or {}).get("id") == oid]
        if mine and (mine[0]["order"].get("status") not in PENDING):
            show("history item for that order", {"status": hist["status"], "data": mine[0]})
            filled = mine[0]["order"].get("filledQuantity")
            fill = mine[0].get("fill") or {}
            value = (fill.get("walletImpact") or {}).get("netValue")
            print(f"Units: fill price {fill.get('price')}, order value {value} (account currency)")
            break
        time.sleep(2)
    print(f"Quantity step: filled {filled}")
    if not filled:
        cres = client.call("DELETE", f"/equity/orders/{oid}", "cancel")
        print(f"Not filled (market closed?): cancel request HTTP {cres['status']}")
        return
    held = client.call("GET", "/equity/positions", "positions")
    show("GET /equity/positions while holding it", held)
    for position in held["data"] if isinstance(held["data"], list) else []:
        if isinstance(position, dict) and ticker in (
            position.get("ticker"),
            (position.get("instrument") or {}).get("ticker"),
        ):
            print(f"Units: {ticker} current price {position.get('currentPrice')} (the fund's public price)")
    sell = {"ticker": ticker, "quantity": -abs(filled), "extendedHours": False}
    sres = client.call("POST", "/equity/orders/market", "market", sell)
    print(f"\nSold it back: HTTP {sres['status']} (check the demo app shows no {ticker} position)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Record Trading 212 demo response shapes (no values)")
    ap.add_argument("--order", metavar="TICKER", help="also check the quantity step with a tiny buy and sell")
    args = ap.parse_args()
    key, secret = os.environ.get("T212_API_KEY"), os.environ.get("T212_API_SECRET")
    if not key or not secret:
        sys.exit("Set T212_API_KEY and T212_API_SECRET (demo key) in your shell first.")
    base = _base_url()
    fast = bool(os.environ.get("T212_TEST_BASE_URL")) and bool(os.environ.get("T212_TEST_FAST"))
    client = Client(key, secret, base, fast=fast)
    print(f"Recording response shapes from {'demo' if 'demo.' in base else 'a local test server'}")
    read_only(client)
    if args.order:
        quantity_step(client, args.order)
    return 0


if __name__ == "__main__":
    sys.exit(main())
