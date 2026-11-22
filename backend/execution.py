import hashlib
import math
from datetime import datetime, timedelta, timezone

import httpx
from psycopg.types.json import Jsonb

from config import settings
from db import ACTIVE, event
from strategy import correlation

TERMINAL = ("filled", "canceled", "expired", "rejected", "replaced", "abandoned")


def apply_broker_order(db, order, broker_order):
    status = broker_order["status"]
    if status not in (*ACTIVE, *TERMINAL):
        status = "unknown"
    filled_qty = float(broker_order.get("filled_qty") or 0)
    filled_price = float(broker_order["filled_avg_price"]) if broker_order.get("filled_avg_price") else None
    db.execute("""UPDATE orders SET status=%s, broker_id=%s, filled_qty=%s, filled_price=%s,
                  updated_at=now() WHERE client_id=%s""",
               (status, broker_order["id"], filled_qty, filled_price, order["client_id"]))
    return status


def reconcile_orders(db, broker, cycle):
    unresolved = []
    rows = db.execute("SELECT * FROM orders WHERE mode=%s AND status=ANY(%s)",
                      (settings.mode, list(ACTIVE))).fetchall()
    for row in rows:
        broker_order = broker.lookup(row["client_id"])
        status = row["status"]
        with db.transaction():
            if broker_order is not None:
                status = apply_broker_order(db, row, broker_order)
            elif settings.mode == "demo":
                status = "abandoned"
                db.execute("UPDATE orders SET status=%s, updated_at=now() WHERE client_id=%s",
                           (status, row["client_id"]))
            elif status != "unknown":
                status = "unknown"
                db.execute("UPDATE orders SET status=%s, updated_at=now() WHERE client_id=%s",
                           (status, row["client_id"]))
            if status != row["status"]:
                event(db, cycle, "order", {"symbol": row["symbol"], "status": status,
                                           "client_id": row["client_id"]})
        if status in ACTIVE:
            unresolved.append({**row, "status": status, "client_order_id": row["client_id"]})
    return unresolved


def submit(db, broker, cycle, slot, signal, price, notional, quantity):
    symbol = signal["symbol"]
    asset_class = settings.universe[symbol]
    # A cycle has at most one candidate per symbol. Keep the ID within Alpaca's 48-character limit.
    suffix = hashlib.blake2s(symbol.encode(), digest_size=4).hexdigest()
    client_id = f"ta-{cycle.hex}-{suffix}"
    order = db.execute("""INSERT INTO orders
                          (client_id,cycle_id,mode,symbol,asset_class,side,reference_price,notional,quantity,status)
                          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'prepared') RETURNING *""",
                       (client_id, cycle, settings.mode, symbol, asset_class, signal["side"],
                        price, notional, quantity)).fetchone()
    try:
        reason = None
        if datetime.now(timezone.utc) >= slot + timedelta(seconds=settings.slot_seconds):
            reason = "Cycle expired before submission"
        elif asset_class == "stock" and settings.mode == "alpaca" and not broker.get("/v2/clock")["is_open"]:
            reason = "US market closed before submission"
        if reason:
            with db.transaction():
                db.execute("UPDATE orders SET status='abandoned', updated_at=now() WHERE client_id=%s",
                           (client_id,))
                event(db, cycle, "order", {"symbol": symbol, "status": "abandoned", "detail": reason})
            return "abandoned"
        result = broker.submit(order)
        with db.transaction():
            state = apply_broker_order(db, order, result)
            event(db, cycle, "order", {"symbol": symbol, "side": signal["side"],
                                       "status": state, "client_id": client_id})
        return state
    except Exception as exc:
        code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        # A duplicate ID or validation response may refer to a prior submission.
        with db.transaction():
            db.execute("UPDATE orders SET status='unknown', updated_at=now() WHERE client_id=%s",
                       (client_id,))
            event(db, cycle, "error", {"stage": "execution", "symbol": symbol, "client_id": client_id,
                                       "error": f"Submission unconfirmed ({code or type(exc).__name__})"})
        return "unknown"


def pending_risk(rows, prices, portfolio):
    by_symbol = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol not in by_symbol:
            by_symbol[symbol] = 0.0
        if row["side"] != "buy":
            continue
        if row.get("notional"):
            value = float(row["notional"])
        else:
            price = row.get("limit_price") or row.get("reference_price") or prices.get(symbol)
            price = price or portfolio["positions"].get(symbol, {}).get("price")
            qty = row.get("quantity") or row.get("qty")
            value = float(qty) * float(price) * 1.02 if qty and price else math.inf
        by_symbol[symbol] += value if value > 0 and math.isfinite(value) else math.inf
    return by_symbol


def risk(db, portfolio, pending, signals, returns, prices, slot):
    equity, cash = portfolio["equity"], portfolio["cash"]
    positions = portfolio["positions"]
    high = db.execute("SELECT max(equity) AS peak FROM portfolios WHERE mode=%s",
                      (settings.mode,)).fetchone()["peak"]
    high = max(high or equity, equity)
    if not all(math.isfinite(value) for value in (equity, cash, high)) or any(
        not all(math.isfinite(p[key]) for key in ("qty", "avg_entry", "price", "market_value"))
        for p in positions.values()
    ):
        raise ValueError("Invalid broker portfolio values")
    drawdown = (high - equity) / high if high > 0 else 1.0
    gross = sum(abs(p["market_value"]) for p in positions.values())
    crypto = sum(abs(p["market_value"]) for p in positions.values() if p["asset_class"] == "crypto")
    reserved = pending_risk(pending, prices, portfolio)
    planned = []
    last_entry = {
        row["symbol"]: row["last"] for row in db.execute(
            """SELECT symbol, max(created_at) AS last FROM orders
               WHERE mode=%s AND side='buy' AND (filled_qty>0 OR status=ANY(%s))
               GROUP BY symbol""", (settings.mode, list(ACTIVE)))}
    for row in sorted(signals, key=lambda row: (row["side"] != "sell", -row["conviction"])):
        symbol = row["symbol"]
        is_crypto = settings.universe[symbol] == "crypto"
        position = positions.get(symbol)
        if not portfolio["can_trade"]:
            result = "Account is blocked"
        elif symbol in reserved:
            result = "Open or unconfirmed order exists"
        elif row["side"] == "sell":
            if not position or position["qty"] < 1e-9:
                result = "No long quantity to exit"
            else:
                planned.append((row, None, position["qty"]))
                reserved[symbol] = 0.0
                result = "Approved"
        elif position:
            result = "Position already exists"
        elif equity <= 0 or cash <= 0:
            result = "Insufficient equity or cash"
        elif drawdown >= settings.drawdown_pct:
            result = "Drawdown limit reached"
        elif symbol in last_entry and slot - last_entry[symbol] < timedelta(minutes=settings.cooldown):
            result = "Entry cooldown"
        else:
            outstanding = sum(reserved.values())
            crypto_reserved = sum(value for name, value in reserved.items()
                                  if name in settings.crypto or name.endswith("USD") or "/" in name)
            caps = [equity * settings.order_pct, equity * settings.symbol_pct, cash - outstanding,
                    equity * settings.gross_pct - gross - outstanding]
            if is_crypto:
                caps.append(equity * settings.crypto_pct - crypto - crypto_reserved)
            size = math.floor(min(caps) * 100) / 100 if math.isfinite(min(caps)) else 0
            result = "Allocation, cash or exposure limit"
            if size >= 10:
                exposures = set(positions) | {name for name, value in reserved.items() if value > 0}
                correlations = [correlation(returns.get(symbol, {}), returns.get(name, {}))
                                for name in sorted(exposures)]
                if any(value is None for value in correlations):
                    result = "Insufficient correlation history for current exposure"
                elif any(abs(value) >= settings.correlation for value in correlations):
                    result = "Correlation limit reached"
                else:
                    planned.append((row, size, None))
                    reserved[symbol] = size
                    result = "Approved"
        row["risk"] = result
    return planned, drawdown


def persist_signals(db, cycle, rows):
    with db.transaction():
        for row in rows:
            db.execute("""INSERT INTO signals VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                          ON CONFLICT (cycle_id,symbol) DO UPDATE
                          SET decision=excluded.decision, conviction=excluded.conviction, risk=excluded.risk""",
                       (cycle, row["symbol"], row["side"], row["strategy"], row["quant_score"],
                        Jsonb(row["decision"]), row["conviction"], row["risk"]))
