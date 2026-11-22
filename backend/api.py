from datetime import datetime, timezone
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
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
        scans = catalog_rows(db, "", "", 0, 10, overview=True)
        catalog = db.execute("""SELECT count(*) AS total,
                                 count(*) FILTER (WHERE asset_class='stock') AS stocks,
                                 count(*) FILTER (WHERE asset_class='crypto') AS crypto
                                 FROM assets WHERE mode=%s""", (settings.mode,)).fetchone()
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
            "interval": settings.slot_seconds, "server_time": datetime.now(timezone.utc), "cycles": cycles, "scans": scans, "catalog": catalog,
            "signals": signals, "orders": orders, "portfolio": history[0] if history else None,
            "equity": [[row["recorded_at"], row["equity"]] for row in reversed(history)],
            "peak_equity": peak, "events": events}


CATALOG_FILTER = """a.mode=%(mode)s AND (%(kind)s='' OR a.asset_class=%(kind)s)
                    AND (%(query)s='' OR strpos(upper(a.symbol),%(query)s)>0
                         OR strpos(upper(a.name),%(query)s)>0)"""


def catalog_rows(db, query, kind, offset, limit, *, overview=False, symbol=None):
    fields = "a.latest_ready - 'returns'" if overview or symbol else "a.latest_ready - 'returns' - 'chart'"
    order = ("(a.last_scan_at IS NOT NULL AND a.scan_status='ready') DESC, "
             "a.last_scan_at DESC NULLS LAST, a.symbol") if overview else "a.symbol"
    return db.execute(f"""SELECT a.symbol,a.asset_class,a.name,a.exchange,a.tradable,a.fractionable,
                                a.last_scan_at,
                           CASE
                             WHEN a.asset_class='stock' AND c.stock_session='closed' THEN 'closed'
                             WHEN a.asset_class='stock' AND c.stock_session='unknown' THEN 'unavailable'
                             WHEN a.last_scan_at IS NULL OR a.last_scan_at < now()-interval '8 minutes'
                               THEN 'queued'
                             ELSE coalesce(a.scan_status,'queued')
                           END AS status,
                           coalesce({fields}, '{{}}'::jsonb) ||
                           jsonb_build_object('reason', CASE
                             WHEN a.asset_class='stock' AND c.stock_session='closed'
                               THEN 'US regular session is closed'
                             WHEN a.asset_class='stock' AND c.stock_session='unknown'
                               THEN 'US market session could not be verified'
                             WHEN a.last_scan_at IS NULL OR a.last_scan_at < now()-interval '8 minutes'
                               THEN 'Waiting for the next scan of this asset'
                             ELSE a.scan_reason
                           END) AS details
                       FROM assets a JOIN asset_catalogs c ON a.mode=c.mode
                       WHERE {CATALOG_FILTER} AND (%(symbol)s::text IS NULL OR a.symbol=%(symbol)s)
                       ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s""",
                      {"mode": settings.mode, "kind": kind, "query": query, "symbol": symbol,
                       "offset": offset, "limit": limit}).fetchall()


@app.get("/markets")
def markets(q: str = Query("", max_length=80), kind: str = Query("", pattern="^(stock|crypto)?$"),
            page: int = Query(1, ge=1, le=2000), symbol: str | None = Query(None, max_length=80)):
    with connect() as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        if symbol:
            rows = catalog_rows(db, "", "", 0, 1, symbol=symbol.upper())
            if not rows:
                raise HTTPException(404, "Asset not found")
            return rows[0]
        query = q.strip().upper()
        total = db.execute(f"SELECT count(*) AS total FROM assets a WHERE {CATALOG_FILTER}",
                           {"mode": settings.mode, "kind": kind, "query": query}).fetchone()["total"]
        page_size = 50
        page = min(page, max(1, (total + page_size - 1) // page_size))
        rows = catalog_rows(db, query, kind, (page - 1) * page_size, page_size)
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


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
