from datetime import timedelta

from psycopg.types.json import Jsonb

from config import settings

REFRESH_INTERVAL = timedelta(hours=6)
MAX_CATALOG_AGE = timedelta(hours=24)


def discover(db, broker, slot):
    state = db.execute("SELECT refreshed_at FROM asset_catalogs WHERE mode=%s", (settings.mode,)).fetchone()
    warning = None
    if settings.mode == "demo" or not state or slot - state["refreshed_at"] >= REFRESH_INTERVAL:
        try:
            if settings.mode == "demo":
                catalog = [{"symbol": symbol, "asset_class": asset_class, "name": symbol,
                            "exchange": None, "tradable": True, "fractionable": True}
                           for symbol, asset_class in settings.universe.items()]
            else:
                catalog = broker.assets()
            if not catalog:
                raise ValueError("Asset catalog is empty")
        except Exception as exc:
            if not state or slot - state["refreshed_at"] >= MAX_CATALOG_AGE:
                raise
            warning = f"Using cached Alpaca asset catalog ({type(exc).__name__})"
        else:
            with db.transaction():
                db.execute("""INSERT INTO assets(mode,symbol,asset_class,name,exchange,tradable,fractionable)
                              SELECT %s,symbol,asset_class,name,exchange,tradable,fractionable
                              FROM jsonb_to_recordset(%s) AS item(symbol text, asset_class text, name text,
                                  exchange text, tradable boolean, fractionable boolean)
                              ON CONFLICT (mode,symbol) DO UPDATE SET asset_class=excluded.asset_class,
                                  name=excluded.name,exchange=excluded.exchange,tradable=excluded.tradable,
                                  fractionable=excluded.fractionable""", (settings.mode, Jsonb(catalog)))
                if not state:
                    db.execute("""WITH prior AS (
                                     SELECT DISTINCT ON (s.symbol) s.symbol,c.slot,s.details
                                     FROM scans s JOIN cycles c ON c.id=s.cycle_id
                                     WHERE c.mode=%s AND c.slot >= %s - interval '14 days'
                                       AND s.status='ready' AND s.details ? 'chart'
                                     ORDER BY s.symbol,c.slot DESC
                                   )
                                   UPDATE assets a SET latest_ready=prior.details,
                                       last_scan_at=prior.slot,scan_status='ready'
                                   FROM prior WHERE a.mode=%s AND a.symbol=prior.symbol
                                     AND a.latest_ready IS NULL""", (settings.mode, slot, settings.mode))
                db.execute("DELETE FROM assets WHERE mode=%s AND NOT (symbol=ANY(%s))",
                           (settings.mode, [asset["symbol"] for asset in catalog]))
                db.execute("""INSERT INTO asset_catalogs(mode,refreshed_at) VALUES (%s,%s)
                              ON CONFLICT (mode) DO UPDATE SET refreshed_at=excluded.refreshed_at""",
                           (settings.mode, slot))
    rows = db.execute("""SELECT symbol,asset_class,name,exchange,tradable,fractionable,last_scan_at
                         FROM assets WHERE mode=%s ORDER BY last_scan_at NULLS FIRST, symbol""",
                      (settings.mode,)).fetchall()
    return {row["symbol"]: row for row in rows}, warning


def eligible_batch(assets, priority, stock_limit, crypto_limit, stock_open):
    buckets = {"stock": [], "crypto": []}
    for asset in assets.values():
        buckets[asset["asset_class"]].append(asset["symbol"])
    selected = []
    for asset_class, limit in (("stock", stock_limit if stock_open else 0), ("crypto", crypto_limit)):
        if not limit:
            continue
        symbols = buckets[asset_class]
        owned = [symbol for symbol in symbols if symbol in priority]
        rotating = [symbol for symbol in symbols if symbol not in priority]
        priority_limit = limit - max(1, limit // 5) if rotating and limit > 1 else limit
        owned = owned[:priority_limit]
        selected.extend(owned + rotating[:limit - len(owned)])
    return {symbol: assets[symbol]["asset_class"] for symbol in selected}


def record_scan(db, cycle, slot, symbol, asset_class, status, details):
    audit = {key: value for key, value in details.items() if key not in {"chart", "returns"}}
    db.execute("INSERT INTO scans VALUES (%s,%s,%s,%s,%s)",
               (cycle, symbol, asset_class, status, Jsonb(audit)))
    db.execute("""UPDATE assets SET last_scan_at=%s, scan_status=%s, scan_reason=%s,
                     latest_ready=coalesce(%s, latest_ready)
                  WHERE mode=%s AND symbol=%s""",
               (slot, status, details.get("reason"), Jsonb(details) if status == "ready" else None,
                settings.mode, symbol))
