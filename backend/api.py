from datetime import datetime, timezone
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from psycopg import Error as DatabaseError
from starlette.concurrency import run_in_threadpool

from config import settings
from db import connect, initialize


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(initialize)
    yield


app = FastAPI(title="Paper Trading Agent", lifespan=lifespan)


@app.get("/healthz")
def health():
    with connect() as db:
        db.execute("SELECT 1")
    return {"status": "ok", "mode": settings.mode}


@app.get("/dashboard")
def dashboard():
    with connect() as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cycles = db.execute("""SELECT * FROM cycles WHERE mode=%s ORDER BY slot DESC LIMIT 20""",
                            (settings.mode,)).fetchall()
        latest = cycles[0]["id"] if cycles else None
        scan_cycle = db.execute("""SELECT c.id FROM cycles c WHERE c.mode=%s AND
                                  EXISTS (SELECT 1 FROM scans s WHERE s.cycle_id=c.id)
                                  ORDER BY c.slot DESC LIMIT 1""", (settings.mode,)).fetchone()
        scans = db.execute("""SELECT symbol,asset_class,status,details-'returns' AS details
                             FROM scans WHERE cycle_id=%s ORDER BY asset_class DESC,symbol""",
                           (scan_cycle["id"] if scan_cycle else None,)).fetchall()
        signals = db.execute("""SELECT * FROM signals WHERE cycle_id=%s
                               ORDER BY conviction DESC NULLS LAST,symbol""", (latest,)).fetchall()
        orders = db.execute("SELECT * FROM orders WHERE mode=%s ORDER BY created_at DESC LIMIT 40",
                            (settings.mode,)).fetchall()
        history = db.execute("""SELECT recorded_at,equity,cash,positions FROM portfolios
                                WHERE mode=%s ORDER BY recorded_at DESC LIMIT 288""",
                             (settings.mode,)).fetchall()
        events = db.execute("SELECT * FROM events WHERE mode=%s ORDER BY id DESC LIMIT 30",
                            (settings.mode,)).fetchall()
        peak = db.execute("SELECT max(equity) AS peak FROM portfolios WHERE mode=%s",
                          (settings.mode,)).fetchone()["peak"]
    return {"mode": settings.mode, "ai": settings.model if settings.uses_openai else "Demo evaluator",
            "interval": settings.slot_seconds, "server_time": datetime.now(timezone.utc), "cycles": cycles, "scans": scans,
            "signals": signals, "orders": orders, "portfolio": history[0] if history else None,
            "equity": [[row["recorded_at"], row["equity"]] for row in reversed(history)],
            "peak_equity": peak, "events": events}


def new_events(after):
    with connect() as db:
        if after is None:
            row = db.execute("SELECT coalesce(max(id),0) AS last FROM events WHERE mode=%s",
                             (settings.mode,)).fetchone()
            return row["last"], []
        rows = db.execute("""SELECT id,kind FROM events WHERE mode=%s AND id>%s
                            ORDER BY id LIMIT 100""", (settings.mode, after)).fetchall()
        return (rows[-1]["id"] if rows else after), rows


@app.websocket("/live")
async def live(socket: WebSocket):
    allowed = os.getenv("DASHBOARD_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    origin = socket.headers.get("origin")
    if origin and origin not in {value.strip() for value in allowed}:
        await socket.close(code=1008)
        return
    await socket.accept()
    try:
        cursor, _ = await run_in_threadpool(new_events, None)
        await socket.send_json({"type": "connected", "cursor": cursor})
        while True:
            try:
                result = await asyncio.wait_for(socket.receive(), timeout=1)
                if result["type"] == "websocket.disconnect":
                    return
            except TimeoutError:
                pass
            cursor, events = await run_in_threadpool(new_events, cursor)
            if events:
                await socket.send_json({"type": "events", "cursor": cursor, "events": events})
    except DatabaseError:
        await socket.close(code=1011)
    except WebSocketDisconnect:
        return
