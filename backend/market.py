import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import httpx

from config import settings

DATA = "https://data.alpaca.markets"
PAPER = "https://paper-api.alpaca.markets"
BAR_SECONDS = 300


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def headers():
    return {"APCA-API-KEY-ID": settings.alpaca_key, "APCA-API-SECRET-KEY": settings.alpaca_secret}


def demo_bars(symbol, slot):
    bases = {"SPY": 595, "QQQ": 514, "AAPL": 225, "MSFT": 418,
             "NVDA": 139, "BTC/USD": 104300, "ETH/USD": 3150}
    seed = int.from_bytes(hashlib.blake2s(symbol.encode(), digest_size=2).digest(), "big")
    end = int(slot.timestamp()) // BAR_SECONDS
    result = []
    base = bases.get(symbol, 100)
    for tick in range(end - 600, end):
        phase = (tick + seed % 367)
        wave = math.sin(phase / 42) * .028 + math.sin(phase / 117) * .046
        close = base * (1 + wave + math.sin(phase * 1.83) * .0017)
        open_ = result[-1].close if result else close * .999
        volume = 650 + 420 * (1 + math.cos(phase / 5)) + 150 * (1 + math.sin(phase * 0.6))
        result.append(Bar(datetime.fromtimestamp(tick * BAR_SECONDS, timezone.utc), open_,
                          max(close, open_) * 1.001, min(close, open_) * .999, close, volume))
    return result


def alpaca_bars(client, symbol, asset_class, slot):
    path = "/v2/stocks/bars" if asset_class == "stock" else "/v1beta3/crypto/us/bars"
    params = {"symbols": symbol, "timeframe": "5Min",
              "start": (slot - timedelta(days=14 if asset_class == "stock" else 3)).isoformat(),
              "end": slot.isoformat(), "limit": 10000}
    if asset_class == "stock":
        params.update(feed=settings.stock_feed, adjustment="split")
    bars = []
    for _ in range(8):
        response = client.get(DATA + path, params=params, headers=headers())
        response.raise_for_status()
        page = response.json()
        bars.extend(Bar(timestamp(row["t"]), *(float(row[key]) for key in ("o", "h", "l", "c", "v")))
                    for row in (page.get("bars") or {}).get(symbol, []))
        if not page.get("next_page_token"):
            return [bar for bar in bars if bar.time + timedelta(seconds=BAR_SECONDS) <= slot]
        params["page_token"] = page["next_page_token"]
    raise ValueError("Market data pagination exceeded limit")


def validate(bars, slot):
    if len(bars) < 400:
        raise ValueError("Insufficient history for the hourly indicators")
    if bars[-1].time != slot - timedelta(seconds=BAR_SECONDS):
        raise ValueError("Latest expected completed bar is missing")
    if any(b.time <= a.time for a, b in zip(bars, bars[1:])):
        raise ValueError("Duplicate or out-of-order market bars")
    if any(b.time - a.time != timedelta(seconds=BAR_SECONDS) for a, b in zip(bars[-4:], bars[-3:])):
        raise ValueError("A recent market bar is missing")
    if any(not all(math.isfinite(x) for x in (b.open, b.high, b.low, b.close, b.volume))
           or b.low <= 0 or b.low > min(b.open, b.close)
           or b.high < max(b.open, b.close) or b.volume < 0 for b in bars):
        raise ValueError("Invalid OHLCV values")


def scan(client, broker, slot):
    session_error = None
    stock_open = False
    if settings.stocks:
        try:
            stock_open = broker.stock_open()
            if stock_open and settings.mode == "alpaca":
                broker.load_sessions(slot)
        except Exception as exc:
            session_error = f"Market clock unavailable ({type(exc).__name__})"

    def fetch(entry):
        symbol, asset_class = entry
        if asset_class == "stock":
            if session_error:
                return symbol, ("unavailable", session_error, [])
            if not stock_open:
                return symbol, ("closed", "US regular session is closed", [])
        try:
            bars = (demo_bars(symbol, slot) if settings.mode == "demo"
                    else alpaca_bars(client, symbol, asset_class, slot))
            if asset_class == "stock" and settings.mode == "alpaca":
                bars = broker.regular_bars(bars)
            validate(bars, slot)
            return symbol, ("ready", None, bars)
        except httpx.HTTPStatusError as exc:
            return symbol, ("unavailable", f"Market data HTTP {exc.response.status_code}", [])
        except ValueError as exc:
            message = str(exc) if type(exc) is ValueError else "Invalid upstream market data"
            return symbol, ("unavailable", message, [])
        except Exception as exc:
            return symbol, ("unavailable", f"Market data error ({type(exc).__name__})", [])

    with ThreadPoolExecutor(max_workers=6) as pool:
        return dict(pool.map(fetch, settings.universe.items()))
