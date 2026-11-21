#!/usr/bin/env python3
"""Phase 0 check of the Trading 212 Public API (beta) - DEMO ACCOUNT ONLY.

What it does (all against the practice/demo environment):
  1. Authenticates and reads the account summary (currency, cash).
  2. Reads exchange schedules and the instrument list; looks up candidate UK ETFs.
  3. Places a tiny fractional MARKET buy (queued and cancelled if the market is shut;
     sold back if it fills) to confirm fractional quantities and negative-quantity sells.
  4. Places LIMIT, STOP and STOP_LIMIT orders far from the market, reads them back,
     cancels them and confirms the cancellation.
  5. Sends one deliberately invalid order to see the error format.
  6. Reads order history, records rate-limit headers, and cancels anything it created.

It never touches the live environment and never prints or saves your key or secret.

Usage (on your own machine, Python 3.11+, no packages needed):
    read -rs T212_API_KEY && export T212_API_KEY        # paste the DEMO key (not echoed,
    read -rs T212_API_SECRET && export T212_API_SECRET  # not saved in shell history)
    python3 phase0_api_check.py                 # pick a candidate ETF automatically
    python3 phase0_api_check.py --ticker XYZ    # or name the Trading 212 ticker
    python3 phase0_api_check.py --ref-price 85  # optional: price to set far-away orders from

Output: a summary on screen and phase0_report.json next to the script.
Neither contains credentials, but both hold demo account data (cash, order IDs, broker
errors): share them privately, never in an issue, PR or Actions log (ADR-0014).
Best run during London market hours (Mon-Fri 08:00-16:30) so the market order can fill.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

DEMO_BASE = "https://demo.trading212.com/api/v0"

# UK-listed ETF codes to look up. Candidates to check availability, not recommendations.
CANDIDATE_SHORTNAMES = [
    "VWRP",
    "VWRL",
    "VEVE",
    "VUSA",
    "ISF",
    "IGLT",
    "INXG",
    "VAGP",
    "SGLN",
    "CMOD",
    "IUKP",
]

# Minimum seconds between calls, from the published per-endpoint limits (with a margin).
MIN_INTERVAL = {
    "summary": 5.5,
    "exchanges": 31,
    "instruments": 51,
    "orders_list": 5.5,
    "order_get": 1.2,
    "positions": 1.2,
    "history": 3.2,
    "market": 1.3,
    "limit": 2.2,
    "stop": 2.2,
    "stop_limit": 2.2,
    "cancel": 1.3,
}

PENDING_STATES = {"LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "PARTIALLY_FILLED", "REPLACING"}


def _base_url() -> str:
    """Demo only. A localhost override exists solely for offline testing."""
    override = os.environ.get("T212_TEST_BASE_URL")
    if override:
        host = urllib.parse.urlparse(override).hostname
        if host not in ("127.0.0.1", "localhost"):
            sys.exit("Refusing T212_TEST_BASE_URL that is not localhost.")
        return override.rstrip("/")
    return DEMO_BASE


class Client:
    def __init__(self, key: str, secret: str, base: str, fast: bool = False):
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self._auth = f"Basic {token}"
        self.base = base
        self.fast = fast
        self._last: dict[str, float] = {}

    def _wait(self, bucket: str) -> None:
        if self.fast:
            return
        gap = MIN_INTERVAL.get(bucket, 1.5)
        last = self._last.get(bucket)
        if last is not None:
            delay = gap - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)

    def call(self, method: str, path: str, bucket: str, body: dict | None = None) -> dict:
        """Return {'status', 'data', 'ratelimit', 'error'}; never raises on HTTP errors."""
        for attempt in range(2):
            self._wait(bucket)
            url = self.base + path  # always the demo host (or the localhost mock in tests)
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method=method)  # noqa: S310 - fixed base
            req.add_header("Authorization", self._auth)
            req.add_header("Accept", "application/json")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            status, raw, headers = 0, b"", {}
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https/localhost base
                    status, raw, headers = resp.status, resp.read(), dict(resp.headers)
            except urllib.error.HTTPError as e:
                status, raw, headers = e.code, e.read(), dict(e.headers or {})
            except urllib.error.URLError as e:
                self._last[bucket] = time.monotonic()
                return {"status": 0, "data": None, "ratelimit": {}, "error": f"network: {e.reason}"}
            self._last[bucket] = time.monotonic()
            rl = {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit")}
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = raw.decode(errors="replace")[:500]
            if status == 429 and attempt == 0 and not self.fast:
                reset = rl.get("x-ratelimit-reset")
                wait = 10.0
                if reset and str(reset).isdigit():
                    wait = max(1.0, min(60.0, int(reset) - time.time() + 1))
                time.sleep(wait)
                continue
            err = None if 200 <= status < 300 else _short(parsed)
            return {"status": status, "data": parsed, "ratelimit": rl, "error": err}
        return {"status": 429, "data": None, "ratelimit": {}, "error": "rate limited twice"}


def _short(obj) -> str:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return (text or "")[:300]


class Report:
    def __init__(self):
        self.steps: list[dict] = []
        self.facts: dict = {}

    def add(self, name: str, ok: bool | None, detail: str, res: dict | None = None) -> None:
        step = {"step": name, "result": {True: "PASS", False: "FAIL", None: "INFO"}[ok], "detail": detail}
        if res is not None:
            step["http_status"] = res.get("status")
            if res.get("ratelimit"):
                step["ratelimit_headers"] = res["ratelimit"]
            if res.get("error"):
                step["error_body"] = res["error"]
        self.steps.append(step)
        print(f"[{step['result']:>4}] {name}: {detail}")


def order_status(client: Client, oid: int) -> tuple[str | None, dict | None]:
    """Status of an order: pending lookup first, then recent history."""
    res = client.call("GET", f"/equity/orders/{oid}", "order_get")
    if res["status"] == 200 and isinstance(res["data"], dict):
        return res["data"].get("status"), res["data"]
    hist = client.call("GET", "/equity/history/orders?limit=50", "history")
    items = (hist["data"] or {}).get("items", []) if isinstance(hist["data"], dict) else []
    for item in items:
        order = item.get("order", item) if isinstance(item, dict) else {}
        if order.get("id") == oid:
            return order.get("status"), order
    return None, None


def place_and_cancel(client: Client, rep: Report, created: list, kind: str, body: dict) -> None:
    label = kind.upper()
    res = client.call("POST", f"/equity/orders/{kind}", kind, body)
    if res["status"] != 200 or not isinstance(res["data"], dict) or "id" not in res["data"]:
        rep.add(f"{label} order accepted", False, f"rejected: {body}", res)
        return
    oid = res["data"]["id"]
    created.append(oid)
    rep.add(
        f"{label} order accepted", True, f"id {oid}, status {res['data'].get('status')}, body {body}", res
    )
    status, _ = order_status(client, oid)
    rep.add(f"{label} order readable by id", status is not None, f"status {status}")
    cres = client.call("DELETE", f"/equity/orders/{oid}", "cancel")
    rep.add(f"{label} order cancel request", 200 <= cres["status"] < 300, f"HTTP {cres['status']}", cres)
    final = None
    for _ in range(5):
        final, _ = order_status(client, oid)
        if final not in PENDING_STATES:
            break
    gone = final in (None, "CANCELLED")
    rep.add(f"{label} order confirmed cancelled", gone, f"final status {final or 'no longer pending'}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Trading 212 demo API check (Phase 0)")
    ap.add_argument("--ticker", help="Trading 212 ticker to test with, e.g. from the candidates list")
    ap.add_argument("--ref-price", type=float, help="Approximate price in the instrument's currency")
    ap.add_argument("--quantity", type=float, default=0.1, help="Fractional market order size (default 0.1)")
    ap.add_argument("--skip-market", action="store_true", help="Skip the market order test")
    ap.add_argument(
        "--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "phase0_report.json")
    )
    args = ap.parse_args()

    key, secret = os.environ.get("T212_API_KEY"), os.environ.get("T212_API_SECRET")
    if not key or not secret:
        sys.exit("Set T212_API_KEY and T212_API_SECRET (demo key) in your shell first.")
    base = _base_url()
    fast = bool(os.environ.get("T212_TEST_BASE_URL")) and bool(os.environ.get("T212_TEST_FAST"))
    client = Client(key, secret, base, fast=fast)
    rep = Report()
    created: list[int] = []
    rep.facts["environment"] = "demo" if base == DEMO_BASE else "local test server"
    rep.facts["run_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    print(f"Trading 212 API check against {rep.facts['environment']} ({base})\n")

    try:
        run_checks(client, rep, created, args)
    finally:
        cleanup(client, rep, created)
    return finish(rep, args.out)


def run_checks(client: Client, rep: Report, created: list, args) -> None:
    # 1. Auth and account
    res = client.call("GET", "/equity/account/summary", "summary")
    if res["status"] in (401, 403):
        rep.add(
            "Authenticate", False, "key rejected or missing scopes; check the demo key's permissions", res
        )
        return
    ok = res["status"] == 200 and isinstance(res["data"], dict)
    summary = res["data"] if ok else {}
    cash = (summary.get("cash") or {}).get("availableToTrade")
    rep.facts["account_currency"] = summary.get("currency")
    rep.add(
        "Authenticate and read account summary",
        ok,
        f"currency {summary.get('currency')}, cash available {cash}",
        res,
    )

    # 2. Exchanges
    res = client.call("GET", "/equity/metadata/exchanges", "exchanges")
    exchanges = res["data"] if isinstance(res["data"], list) else []
    london = [e for e in exchanges if "london" in str(e.get("name", "")).lower()]
    rep.facts["exchanges"] = len(exchanges)
    rep.facts["london_exchanges"] = [e.get("name") for e in london]
    rep.add(
        "Read exchange schedules",
        res["status"] == 200,
        f"{len(exchanges)} exchanges; London: {rep.facts['london_exchanges']}",
        res,
    )

    # 3. Instruments and candidate ETFs
    res = client.call("GET", "/equity/metadata/instruments", "instruments")
    instruments = res["data"] if isinstance(res["data"], list) else []
    etfs = [i for i in instruments if i.get("type") == "ETF"]
    by_ccy: dict[str, int] = {}
    for i in etfs:
        by_ccy[i.get("currencyCode", "?")] = by_ccy.get(i.get("currencyCode", "?"), 0) + 1
    found = []
    for code in CANDIDATE_SHORTNAMES:
        for i in etfs:
            if str(i.get("shortName", "")).upper() == code:
                found.append(
                    {
                        k: i.get(k)
                        for k in (
                            "shortName",
                            "ticker",
                            "name",
                            "currencyCode",
                            "isin",
                            "maxOpenQuantity",
                            "extendedHours",
                            "workingScheduleId",
                        )
                    }
                )
    rep.facts["instruments_total"] = len(instruments)
    rep.facts["etfs_by_currency"] = dict(sorted(by_ccy.items(), key=lambda kv: -kv[1]))
    rep.facts["candidate_etfs_found"] = found
    rep.add(
        "Read instrument list",
        res["status"] == 200,
        f"{len(instruments)} instruments, {len(etfs)} ETFs; candidates found: "
        f"{[f['shortName'] + '=' + str(f['ticker']) for f in found]}",
        res,
    )

    ticker = args.ticker or (found[0]["ticker"] if found else None)
    if not ticker:
        rep.add("Choose test ticker", False, "no candidate ETF found; rerun with --ticker")
        return
    inst = next((i for i in instruments if i.get("ticker") == ticker), {})
    rep.facts["test_ticker"] = ticker
    rep.facts["test_ticker_currency"] = inst.get("currencyCode")
    rep.add("Choose test ticker", True, f"{ticker} ({inst.get('name')}, {inst.get('currencyCode')})")

    # 4. Existing pending orders (left untouched)
    res = client.call("GET", "/equity/orders", "orders_list")
    pre = res["data"] if isinstance(res["data"], list) else []
    rep.add(
        "List pending orders before test",
        res["status"] == 200,
        f"{len(pre)} pending orders already on the account (left untouched)",
        res,
    )

    # 5. Positions -> reference price
    ref = args.ref_price
    res = client.call("GET", "/equity/positions", "positions")
    positions = res["data"] if isinstance(res["data"], list) else []
    pos = _find_position(positions, ticker)
    if ref is None and pos:
        ref = pos.get("currentPrice")
    rep.add(
        "Read positions",
        res["status"] == 200,
        f"{len(positions)} positions; reference price {ref if ref else 'unknown yet'}",
        res,
    )

    # 6. Fractional market order
    if not args.skip_market:
        q = args.quantity
        res = client.call(
            "POST",
            "/equity/orders/market",
            "market",
            {"ticker": ticker, "quantity": q, "extendedHours": False},
        )
        if res["status"] == 200 and isinstance(res["data"], dict) and "id" in res["data"]:
            oid = res["data"]["id"]
            created.append(oid)
            rep.add("Fractional MARKET buy accepted", True, f"id {oid}, quantity {q}", res)
            status = None
            for _ in range(10):
                status, _ = order_status(client, oid)
                if status not in PENDING_STATES:
                    break
            if status == "FILLED":
                rep.add("MARKET buy filled", True, "filled")
                res = client.call("GET", "/equity/positions", "positions")
                pos = _find_position(res["data"] if isinstance(res["data"], list) else [], ticker)
                if pos and ref is None:
                    ref = pos.get("currentPrice")
                sres = client.call(
                    "POST",
                    "/equity/orders/market",
                    "market",
                    {"ticker": ticker, "quantity": -q, "extendedHours": False},
                )
                sold = sres["status"] == 200
                if sold and isinstance(sres["data"], dict) and "id" in sres["data"]:
                    created.append(sres["data"]["id"])
                rep.add("MARKET sell (negative quantity) accepted", sold, f"quantity {-q}", sres)
            elif status in PENDING_STATES:
                rep.add("MARKET buy filled", None, f"still {status}: market probably closed; cancelling")
                cres = client.call("DELETE", f"/equity/orders/{oid}", "cancel")
                rep.add(
                    "Queued MARKET order cancelled",
                    200 <= cres["status"] < 300,
                    f"HTTP {cres['status']}",
                    cres,
                )
            else:
                rep.add("MARKET buy filled", False, f"final status {status}")
        else:
            rep.add("Fractional MARKET buy accepted", False, f"quantity {q} rejected", res)

    # 7-9. Far-away orders, read back and cancel
    if ref:
        low, high = round(ref * 0.5, 2), round(ref * 2.0, 2)
        rep.facts["reference_price"] = ref
    else:
        low, high = 1.0, 1_000_000.0
        rep.add("Reference price", None, "unknown (market closed, no position); using 1 and 1,000,000")
    place_and_cancel(
        client,
        rep,
        created,
        "limit",
        {"ticker": ticker, "quantity": 1, "limitPrice": low, "timeValidity": "GOOD_TILL_CANCEL"},
    )
    place_and_cancel(
        client,
        rep,
        created,
        "stop",
        {"ticker": ticker, "quantity": 1, "stopPrice": high, "timeValidity": "DAY"},
    )
    place_and_cancel(
        client,
        rep,
        created,
        "stop_limit",
        {
            "ticker": ticker,
            "quantity": 1,
            "stopPrice": high,
            "limitPrice": round(high * 1.05, 2),
            "timeValidity": "GOOD_TILL_CANCEL",
        },
    )

    # 10. Error format
    res = client.call(
        "POST",
        "/equity/orders/limit",
        "limit",
        {"ticker": "NOT_A_REAL_TICKER", "quantity": 1, "limitPrice": 1.0, "timeValidity": "DAY"},
    )
    rep.add("Invalid order rejected cleanly", 400 <= res["status"] < 500, f"HTTP {res['status']}", res)

    # 11. History
    res = client.call("GET", "/equity/history/orders?limit=5", "history")
    items = (res["data"] or {}).get("items", []) if isinstance(res["data"], dict) else []
    has_next = isinstance(res["data"], dict) and bool(res["data"].get("nextPagePath"))
    rep.add(
        "Read order history",
        res["status"] == 200,
        f"{len(items)} items on first page; next page link: {has_next}",
        res,
    )


def _find_position(positions: list, ticker: str) -> dict | None:
    for p in positions:
        t = p.get("ticker") or (p.get("instrument") or {}).get("ticker")
        if t == ticker:
            return p
    return None


def cleanup(client: Client, rep: Report, created: list) -> None:
    """Cancel any order this script created that is still pending."""
    if not created:
        return
    res = client.call("GET", "/equity/orders", "orders_list")
    pending = res["data"] if isinstance(res["data"], list) else []
    left = [o.get("id") for o in pending if o.get("id") in created]
    for oid in left:
        client.call("DELETE", f"/equity/orders/{oid}", "cancel")
    if left:
        res = client.call("GET", "/equity/orders", "orders_list")
        pending = res["data"] if isinstance(res["data"], list) else []
        left = [o.get("id") for o in pending if o.get("id") in created]
    rep.add(
        "Cleanup: no test orders left pending",
        not left,
        "all test orders cancelled or filled" if not left else f"still pending: {left} - cancel in the app",
    )


def finish(rep: Report, out: str) -> int:
    fails = [s for s in rep.steps if s["result"] == "FAIL"]
    report = {
        "facts": rep.facts,
        "steps": rep.steps,
        "summary": {
            "pass": sum(s["result"] == "PASS" for s in rep.steps),
            "fail": len(fails),
            "info": sum(s["result"] == "INFO" for s in rep.steps),
        },
    }
    with open(out, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(
        f"\n{report['summary']['pass']} passed, {report['summary']['fail']} failed, "
        f"{report['summary']['info']} info. Report written to {out}"
    )
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
