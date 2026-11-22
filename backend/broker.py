from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from config import settings
from market import PAPER, headers, timestamp

NY = ZoneInfo("America/New_York")


def normalized_symbol(value):
    return next((symbol for symbol in settings.crypto if symbol.replace("/", "") == value), value)


def decimal_string(value, digits):
    quantum = Decimal(10) ** -digits
    return str(Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN))


class Alpaca:
    def __init__(self, client):
        self.client = client
        self.clock = None
        self.sessions = None

    def get(self, path, params=None):
        response = self.client.get(PAPER + path, params=params, headers=headers())
        response.raise_for_status()
        return response.json()

    def stock_open(self):
        if self.clock is None:
            self.clock = self.get("/v2/clock")
        return self.clock["is_open"]

    def regular_bars(self, bars):
        return [bar for bar in bars if self.in_session(bar.time.astimezone(NY), self.sessions)]

    @staticmethod
    def in_session(local_time, sessions):
        bounds = sessions.get(local_time.date().isoformat())
        return bool(bounds and bounds[0] <= local_time.strftime("%H:%M") < bounds[1])

    def load_sessions(self, slot):
        if settings.stocks and self.stock_open():
            day = slot.astimezone(NY).date()
            self.sessions = {row["date"]: (row["open"], row["close"]) for row in
                             self.get("/v2/calendar", {"start": day - timedelta(days=14), "end": day})}

    def portfolio(self, db, prices):
        account = self.get("/v2/account")
        positions = {}
        for row in self.get("/v2/positions"):
            symbol = normalized_symbol(row["symbol"])
            positions[symbol] = {"qty": float(row["qty"]), "avg_entry": float(row["avg_entry_price"]),
                                 "price": float(row["current_price"]),
                                 "market_value": float(row["market_value"]),
                                 "asset_class": "crypto" if row["asset_class"] == "crypto" else "stock"}
        return {"equity": float(account["equity"]), "cash": float(account["cash"]),
                "positions": positions, "can_trade": not (account["trading_blocked"]
                or account.get("trade_suspended_by_user") or account.get("account_blocked"))}

    def open_orders(self):
        rows = self.get("/v2/orders", {"status": "open", "limit": 500})
        if len(rows) == 500:
            raise ValueError("Open order count reached the Alpaca query limit")
        output = []
        for row in rows:
            if row.get("legs"):
                raise ValueError("An external multi-leg order needs manual review")
            output.append({**row, "symbol": normalized_symbol(row["symbol"])})
        return output

    def lookup(self, client_id):
        response = self.client.get(PAPER + "/v2/orders:by_client_order_id",
                                   params={"client_order_id": client_id}, headers=headers())
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    def submit(self, order):
        payload = {"symbol": order["symbol"], "side": order["side"], "type": "market",
                   "time_in_force": "gtc" if order["asset_class"] == "crypto" else "day",
                   "client_order_id": order["client_id"]}
        if order["side"] == "buy":
            payload["notional"] = decimal_string(order["notional"], 2)
        else:
            payload["qty"] = decimal_string(order["quantity"], 9)
        response = self.client.post(PAPER + "/v2/orders", headers=headers(), json=payload)
        response.raise_for_status()
        return response.json()


class Demo:
    def stock_open(self):
        return True

    def portfolio(self, db, prices):
        cash = 100_000.0
        positions = defaultdict(lambda: {"qty": 0.0, "avg_entry": 0.0})
        rows = db.execute("SELECT * FROM orders WHERE mode='demo' AND status='filled' ORDER BY created_at, client_id")
        for row in rows:
            symbol, quantity, price = row["symbol"], row["filled_qty"], row["filled_price"]
            position = positions[symbol]
            if row["side"] == "buy":
                cash -= quantity * price
                position["avg_entry"] = (position["avg_entry"] * position["qty"] + quantity * price) / (
                    position["qty"] + quantity)
                position["qty"] += quantity
            else:
                cash += quantity * price
                position["qty"] -= quantity
            positions[symbol].update(price=prices.get(symbol, price), asset_class=row["asset_class"])
        active = {symbol: {**p, "market_value": p["qty"] * p["price"]}
                  for symbol, p in positions.items() if p["qty"] > 1e-10}
        return {"equity": cash + sum(p["market_value"] for p in active.values()), "cash": cash,
                "positions": active, "can_trade": True}

    def open_orders(self):
        return []

    def lookup(self, client_id):
        return None

    def submit(self, order):
        price = order["reference_price"] * (1.0004 if order["side"] == "buy" else .9996)
        qty = order["notional"] / price if order["side"] == "buy" else order["quantity"]
        return {"id": order["client_id"], "status": "filled", "filled_qty": qty,
                "filled_avg_price": price}
