import logging
import signal
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from psycopg.types.json import Jsonb

from broker import Alpaca, Demo
from config import settings
from db import connect, event, initialize, step
from execution import persist_signals, reconcile_orders, risk, submit
from market import scan, timestamp
from strategy import candidates, evaluate, features, return_samples
from universe import discover, eligible_batch, record_scan

LOGGER = logging.getLogger("trading.worker")
STOP = threading.Event()


def safe_error(exc):
    return (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
            else type(exc).__name__)


def previous_returns(db, symbol):
    row = db.execute("""SELECT latest_ready->'returns' AS samples FROM assets
                        WHERE mode=%s AND symbol=%s
                          AND (latest_ready->>'bar_time')::timestamptz >= now() - interval '7 days'""",
                     (settings.mode, symbol)).fetchone()
    if row is None:
        row = db.execute("""SELECT s.details->'returns' AS samples FROM scans s JOIN cycles c ON s.cycle_id=c.id
                            WHERE c.mode=%s AND s.symbol=%s AND s.status='ready'
                              AND c.slot >= now() - interval '7 days' AND s.details ? 'returns'
                            ORDER BY c.slot DESC LIMIT 1""", (settings.mode, symbol)).fetchone()
    return {timestamp(time): value for time, value in row["samples"]} if row and row["samples"] else {}


def run_cycle(db, client, cycle, slot):
    broker = Demo() if settings.mode == "demo" else Alpaca(client)
    summary = {"universe": 0, "ready": 0, "scanned": 0, "unavailable": 0, "candidates": 0,
               "eligible": 0, "orders": 0, "unconfirmed": 0, "errors": 0, "ai": "openai" if settings.uses_openai else "demo"}
    prices = {}
    current = "initialize"
    status = "completed"
    try:
        step(db, cycle, current, detail="PostgreSQL lock acquired")
        current = "scan"
        assets, warning = discover(db, broker, slot)
        summary["universe"] = len(assets)
        stock_session = "unknown"
        priority = set()
        if settings.mode == "alpaca":
            broker.set_catalog(assets)
            try:
                priority = broker.held_symbols()
            except Exception as exc:
                summary["errors"] += 1
                warning = f"Could not prioritize held assets ({safe_error(exc)})"
        try:
            stock_session = "open" if broker.stock_open() else "closed"
            if stock_session == "open" and settings.mode == "alpaca":
                broker.load_sessions(slot)
        except Exception as exc:
            stock_session = "unknown"
            summary["errors"] += 1
            warning = f"Stock calendar unavailable ({safe_error(exc)})"
        db.execute("UPDATE asset_catalogs SET stock_session=%s WHERE mode=%s", (stock_session, settings.mode))
        selected = eligible_batch(assets, priority, settings.stock_scan_limit, settings.crypto_scan_limit,
                                  stock_session == "open")
        data = scan(client, broker, slot, selected)
        summary["closed"] = sum(row["asset_class"] == "stock" for row in assets.values()) if stock_session == "closed" else 0
        summary["unavailable"] = sum(row[0] == "unavailable" for row in data.values())
        summary["scanned"] = sum(row[0] != "deferred" for row in data.values())
        summary["queued"] = summary["universe"] - summary["scanned"] - summary["closed"]
        detail = f"{summary['scanned']} of {len(assets)} catalog assets checked"
        step(db, cycle, current, "degraded" if warning else "done",
             f"{detail}. {warning}" if warning else detail)

        current = "indicators"
        indicators, returns = {}, {}
        with db.transaction():
            for symbol, (state, reason, bars) in data.items():
                if state == "deferred":
                    continue
                details = {"reason": reason}
                if state == "ready":
                    try:
                        indicators[symbol] = features(bars)
                        returns[symbol] = return_samples(bars)
                        details.update(features=indicators[symbol], bar_time=bars[-1].time.isoformat(),
                                       chart=[[b.time.isoformat(), b.close] for b in bars[-288:]],
                                       returns=[[t.isoformat(), v] for t, v in list(returns[symbol].items())[-700:]])
                    except ValueError as exc:
                        state, details = "unavailable", {"reason": str(exc)}
                        summary["unavailable"] += 1
                record_scan(db, cycle, slot, symbol, assets[symbol]["asset_class"], state, details)
        summary["ready"] = len(indicators)
        step(db, cycle, current, "degraded" if summary["unavailable"] else "done",
             f"{len(indicators)} assets passed validation")

        current = "signals"
        prices = {symbol: row["price"] for symbol, row in indicators.items()
                  if assets[symbol]["asset_class"] == "stock" or symbol.endswith("/USD")}
        portfolio = broker.portfolio(db, prices)
        signals = candidates(indicators, portfolio["positions"])
        summary["candidates"] = len(signals)
        for row in signals:
            asset = assets[row["symbol"]]
            row.update(asset_class=asset["asset_class"], decision={"action": "pending"},
                       conviction=None, risk="Pending evaluation")
            if not asset["tradable"]:
                row["risk"] = "Asset is not tradable on Alpaca"
            elif asset["asset_class"] == "crypto" and not row["symbol"].endswith("/USD"):
                row["risk"] = "Paper orders require a USD quote"
            elif asset["asset_class"] == "stock" and not asset["fractionable"] and row["side"] == "buy":
                row["risk"] = "Stock is not eligible for fractional orders"
        persist_signals(db, cycle, signals)
        step(db, cycle, current, detail=f"{len(signals)} candidates")

        current = "ai"
        evaluable = [row for row in signals if row["risk"] == "Pending evaluation"]
        entries = sorted((row for row in evaluable if row["side"] == "buy"),
                         key=lambda row: (-row["quant_score"], row["symbol"]))
        exits = [row for row in evaluable if row["side"] == "sell"]
        review = exits + entries[:24]
        try:
            context = {"stock_session_open": stock_session == "open",
                "assets": {symbol: {"return_1h": f["return_1h"], "rsi": f["rsi"]}
                           for symbol, f in indicators.items()}}
            decisions = evaluate(client, review, indicators, context)
            step(db, cycle, current, detail=f"{len(decisions)} evaluations")
        except Exception as exc:
            summary["errors"] += 1
            decisions = {row["symbol"]: {"action": "reject", "score": 0, "source": "unavailable",
                                         "reason": f"AI evaluation failed ({safe_error(exc)})"} for row in review}
            step(db, cycle, current, "degraded", safe_error(exc))

        current = "conviction"
        qualified = []
        for row in signals:
            decision = decisions.get(row["symbol"])
            if decision is None:
                if row["risk"] == "Pending evaluation":
                    row["risk"] = "AI review limit reached"
                row["decision"] = {"action": "skipped", "reason": row["risk"]}
                continue
            conviction = round(.65 * row["quant_score"] + .35 * decision["score"], 1)
            row.update(decision=decision, conviction=conviction, risk="Signal quality gate")
            # Protective exits still require a valid market bar and deterministic account checks.
            if row["side"] == "sell" or (decision["action"] == "approve"
                    and decision["score"] >= settings.min_ai and conviction >= settings.min_conviction):
                row["risk"] = "Pending risk checks"
                qualified.append(row)
        persist_signals(db, cycle, signals)
        summary["eligible"] = len(qualified)
        step(db, cycle, current, detail=f"{len(qualified)} passed signal rules")

        current = "risk"
        recovered = reconcile_orders(db, broker, cycle)
        pending = {row["client_order_id"]: row for row in recovered}
        pending.update({row["client_order_id"]: row for row in broker.open_orders()})
        # Re-read holdings after checking order transitions from the previous slot.
        portfolio = broker.portfolio(db, prices)
        usd_returns = {symbol: returns[symbol] for symbol in prices}
        for symbol in set(portfolio["positions"]) | {row["symbol"] for row in pending.values()}:
            if symbol not in usd_returns and ("/" not in symbol or symbol.endswith("/USD")):
                usd_returns[symbol] = previous_returns(db, symbol)
        planned, drawdown = risk(db, portfolio, pending.values(), qualified, usd_returns, prices, slot)
        summary["drawdown"] = drawdown
        persist_signals(db, cycle, signals)
        step(db, cycle, current, detail=f"{len(planned)} approved by portfolio limits")

        current = "execution"
        for row, notional, quantity in planned:
            result = submit(db, broker, cycle, slot, row, prices[row["symbol"]], notional, quantity)
            summary["orders"] += int(result not in {"unknown", "abandoned", "rejected"})
            summary["unconfirmed"] += int(result == "unknown")
        step(db, cycle, current, "degraded" if summary["unconfirmed"] else "done",
             f"{summary['orders']} broker-confirmed submissions, {summary['unconfirmed']} unconfirmed")
    except Exception as exc:
        summary["errors"] += 1
        status = "failed"
        step(db, cycle, current, "failed", safe_error(exc))
        event(db, cycle, "error", {"stage": current, "error": safe_error(exc)})
        LOGGER.error("Cycle %s stage %s failed (%s)", cycle, current, safe_error(exc))

    try:
        remaining = reconcile_orders(db, broker, cycle)
        summary["open_orders"] = len(remaining)
        portfolio = broker.portfolio(db, prices)
        db.execute("INSERT INTO portfolios(cycle_id,mode,equity,cash,positions) VALUES (%s,%s,%s,%s,%s)",
                   (cycle, settings.mode, portfolio["equity"], portfolio["cash"], Jsonb(portfolio["positions"])))
        step(db, cycle, "reconciliation", detail=f"{len(portfolio['positions'])} open positions")
    except Exception as exc:
        status = "failed"
        summary["errors"] += 1
        step(db, cycle, "reconciliation", "failed", safe_error(exc))
        event(db, cycle, "error", {"stage": "reconciliation", "error": safe_error(exc)})

    if status == "completed" and (summary["unavailable"] or summary["unconfirmed"] or summary["errors"]):
        status = "degraded"
    notification = "Telegram disabled"
    if settings.telegram_token:
        try:
            response = client.post(f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage",
                                   json={"chat_id": settings.telegram_chat_id,
                                         "text": (f"Paper trading · {settings.mode} · {status}\n"
                                                  f"{slot.astimezone(ZoneInfo('Asia/Bangkok')):%Y-%m-%d %H:%M} UTC+7 · {str(cycle)[:8]}\n"
                                                  f"Assets ready {summary['ready']}/{summary['scanned']} scanned of {summary['universe']} listed\n"
                                                  f"Candidates {summary['candidates']} · Orders {summary['orders']}\n"
                                                  f"Unconfirmed {summary['unconfirmed']} · Errors {summary['errors']}")})
            response.raise_for_status()
            if not response.json().get("ok"):
                raise ValueError("Telegram refused the summary")
            notification = "Telegram summary sent"
        except Exception as exc:
            summary["errors"] += 1
            notification = f"Telegram delivery failed ({safe_error(exc)})"
            status = "degraded" if status == "completed" else status
            event(db, cycle, "error", {"stage": "logging", "error": notification})
    with db.transaction():
        step(db, cycle, "logging", "degraded" if "failed" in notification else "done", notification)
        step(db, cycle, "dashboard", detail="Portfolio and cycle events published")
        db.execute("UPDATE cycles SET status=%s, summary=%s, finished_at=now() WHERE id=%s",
                   (status, Jsonb(summary), cycle))
        event(db, cycle, "cycle", {"status": status, **summary})
    LOGGER.info("Cycle %s %s %s", cycle, status, summary)


def process_slot(slot):
    lock = 61415 if settings.mode == "demo" else 61416
    with connect() as db, httpx.Client(timeout=10, trust_env=False) as client:
        if not db.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (lock,)).fetchone()["acquired"]:
            return
        with db.transaction():
            interrupted = db.execute("""UPDATE cycles SET status='interrupted', finished_at=now(),
                                        summary=summary || '{"interrupted": true}'::jsonb
                                        WHERE mode=%s AND status='running' RETURNING id""",
                                     (settings.mode,)).fetchall()
            for row in interrupted:
                event(db, row["id"], "cycle", {"status": "interrupted"})
            cycle = uuid4()
            inserted = db.execute("""INSERT INTO cycles(id,mode,slot,status) VALUES (%s,%s,%s,'running')
                                     ON CONFLICT (mode,slot) DO NOTHING RETURNING id""",
                                  (cycle, settings.mode, slot)).fetchone()
            if not inserted:
                return
            event(db, cycle, "cycle", {"status": "running"})
        run_cycle(db, client, cycle, slot)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: STOP.set())
    initialize()
    LOGGER.info("Worker ready (%s). Interval %ss.", settings.mode, settings.slot_seconds)
    while not STOP.is_set():
        now = datetime.now(timezone.utc).timestamp()
        # A startup during the publication grace period waits for the current bar.
        elapsed = now % settings.slot_seconds
        if elapsed < 5:
            STOP.wait(5 - elapsed)
            continue
        slot = datetime.fromtimestamp(int(now // settings.slot_seconds) * settings.slot_seconds, timezone.utc)
        try:
            process_slot(slot)
        except Exception as exc:
            LOGGER.error("Worker cycle interrupted (%s)", safe_error(exc))
        wait = (slot + timedelta(seconds=settings.slot_seconds + 5)
                - datetime.now(timezone.utc)).total_seconds()
        STOP.wait(max(0, wait))


if __name__ == "__main__":
    main()
