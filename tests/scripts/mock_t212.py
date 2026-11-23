"""Minimal local stand-in for the Trading 212 demo API, for offline testing only.

MOCK_MARKET=open  -> market orders fill immediately
MOCK_MARKET=closed -> market orders stay NEW until cancelled
"""

import base64
import itertools
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

KEY, SECRET = "test-key", "test-secret"
MARKET = os.environ.get("MOCK_MARKET", "open")
ids = itertools.count(1000)
pending = {}
history = []
positions = {}
PRICE = {"ISF_EQ": 820.0}  # GBX
INSTRUMENTS = [
    {
        "ticker": "ISF_EQ",
        "type": "ETF",
        "currencyCode": "GBX",
        "isin": "IE0005042456",
        "name": "iShares Core FTSE 100",
        "shortName": "ISF",
        "maxOpenQuantity": 100000,
        "extendedHours": False,
        "workingScheduleId": 1,
    },
    {
        "ticker": "AAPL_US_EQ",
        "type": "STOCK",
        "currencyCode": "USD",
        "isin": "US0378331005",
        "name": "Apple",
        "shortName": "AAPL",
        "maxOpenQuantity": 1000,
        "extendedHours": True,
        "workingScheduleId": 2,
    },
]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-ratelimit-limit", "50")
        self.send_header("x-ratelimit-remaining", "49")
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        want = "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
        if self.headers.get("Authorization") != want:
            self._send(401, {"code": "AuthenticationFailed"})
            return False
        return True

    def do_GET(self):  # noqa: PLR0911 - one return per mocked route
        if not self._authed():
            return
        p = self.path.split("?")[0].replace("/api/v0", "")
        if p == "/equity/account/summary":
            return self._send(
                200,
                {
                    "id": 1,
                    "currency": "GBP",
                    "totalValue": 5000,
                    "cash": {"availableToTrade": 5000, "inPies": 0, "reservedForOrders": 0},
                },
            )
        if p == "/equity/metadata/exchanges":
            return self._send(200, [{"id": 1, "name": "London Stock Exchange", "workingSchedules": []}])
        if p == "/equity/metadata/instruments":
            return self._send(200, INSTRUMENTS)
        if p == "/equity/orders":
            return self._send(200, list(pending.values()))
        if p.startswith("/equity/orders/"):
            oid = int(p.rsplit("/", 1)[1])
            return self._send(200, pending[oid]) if oid in pending else self._send(404, {"code": "NotFound"})
        if p == "/equity/positions":
            return self._send(
                200,
                [
                    {"instrument": {"ticker": t}, "quantity": q, "currentPrice": PRICE[t]}
                    for t, q in positions.items()
                    if q
                ],
            )
        if p == "/equity/history/orders":
            items = [h if "order" in h else {"order": h} for h in history[-50:]]
            return self._send(200, {"items": items, "nextPagePath": None})
        if p == "/history/transactions":
            return self._send(
                200,
                {
                    "items": [
                        {
                            "type": "DEPOSIT",
                            "amount": 1000,
                            "reference": "ref-1",
                            "dateTime": "2026-11-23T08:00:00Z",
                        },
                        {
                            "type": "WITHDRAW",
                            "amount": -10,
                            "reference": "ref-2",
                            "dateTime": "2026-11-23T09:00:00Z",
                        },
                        {
                            "type": "DEPOSIT",
                            "amount": 10,
                            "reference": "ref-3",
                            "dateTime": "2026-11-23T10:00:00Z",
                        },
                    ],
                    "nextPagePath": None,
                },
            )
        self._send(404, {"code": "NoRoute"})

    def do_POST(self):
        if not self._authed():
            return
        p = self.path.replace("/api/v0", "")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        kind = p.rsplit("/", 1)[1]
        if body.get("ticker") not in PRICE:
            return self._send(400, {"code": "InstrumentNotFound", "clarification": "unknown ticker"})
        if round(body["quantity"], 3) != body["quantity"]:  # Trading 212 refuses a fourth decimal
            return self._send(400, {"type": "/api-errors/quantity-precision-mismatch", "status": 400})
        oid = next(ids)
        order = {
            "id": oid,
            "ticker": body["ticker"],
            "quantity": body["quantity"],
            "type": kind.upper(),
            "status": "NEW",
            "limitPrice": body.get("limitPrice"),
            "stopPrice": body.get("stopPrice"),
        }
        if kind == "market" and MARKET == "open":
            order["status"] = "FILLED"
            order["filledQuantity"] = body["quantity"]
            positions[body["ticker"]] = positions.get(body["ticker"], 0) + body["quantity"]
            price = PRICE[body["ticker"]]
            fill = {
                "price": price,
                "walletImpact": {"netValue": round(abs(body["quantity"]) * price / 100, 2)},
            }
            history.append({"order": order, "fill": fill})  # GBX: the value is in pounds
            return self._send(200, order)
        else:
            pending[oid] = order
        self._send(200, order)

    def do_DELETE(self):
        if not self._authed():
            return
        oid = int(self.path.rsplit("/", 1)[1])
        if oid not in pending:
            return self._send(404, {"code": "NotFound"})
        o = pending.pop(oid)
        o["status"] = "CANCELLED"
        history.append(o)
        self._send(200)


if __name__ == "__main__":
    port = int(sys.argv[1])
    HTTPServer(("127.0.0.1", port), H).serve_forever()
