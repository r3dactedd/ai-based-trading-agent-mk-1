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


def alpaca_bars(client, symbols, asset_class, slot, deadline):
    path = "/v2/stocks/bars" if asset_class == "stock" else "/v1beta3/crypto/us/bars"
    params = {"symbols": ",".join(symbols), "timeframe": "5Min",
              "start": (slot - timedelta(days=14 if asset_class == "stock" else 3)).isoformat(),
              "end": slot.isoformat(), "limit": 10000}
    if asset_class == "stock":
        params.update(feed=settings.stock_feed, adjustment="split")
    bars = {symbol: [] for symbol in symbols}
    for _ in range(8):
        if datetime.now(timezone.utc) >= deadline:
            raise TimeoutError("Market scan deadline")
        response = client.get(DATA + path, params=params, headers=headers())
        response.raise_for_status()
        page = response.json()
        for symbol, rows in (page.get("bars") or {}).items():
            if symbol not in bars:
                continue
            bars[symbol].extend(
                Bar(timestamp(row["t"]), *(float(row[key]) for key in ("o", "h", "l", "c", "v")))
                for row in rows
            )
        if not page.get("next_page_token"):
            last_start = slot - timedelta(seconds=BAR_SECONDS)
            return {symbol: [bar for bar in group if bar.time <= last_start]
                    for symbol, group in bars.items()}
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


def scan(client, broker, slot, selected):
    deadline = min(datetime.now(timezone.utc) + timedelta(seconds=120),
                   slot + timedelta(seconds=settings.slot_seconds - 45))

    def check(bars):
        try:
            validate(bars, slot)
            return "ready", None, bars
        except ValueError as exc:
            return "unavailable", str(exc), []

    if settings.mode == "demo":
        return {symbol: check(demo_bars(symbol, slot)) for symbol in selected}

    def fetch(batch):
        asset_class, symbols = batch
        try:
            results = alpaca_bars(client, symbols, asset_class, slot, deadline)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in {400, 422} and len(symbols) > 1:
                half = len(symbols) // 2
                return {**fetch((asset_class, symbols[:half])), **fetch((asset_class, symbols[half:]))}
            return dict.fromkeys(symbols, ("unavailable", f"Market data HTTP {code}", []))
        except TimeoutError:
            return dict.fromkeys(symbols, ("deferred", "Scan deadline reached", []))
        except Exception as exc:
            reason = f"Market data error ({type(exc).__name__})"
            return dict.fromkeys(symbols, ("unavailable", reason, []))

        return {symbol: check(broker.regular_bars(bars) if asset_class == "stock" else bars)
                for symbol, bars in results.items()}

    batches = []
    for asset_class, size in (("crypto", 16), ("stock", 8)):
        symbols = [symbol for symbol, kind in selected.items() if kind == asset_class]
        batches.extend((asset_class, symbols[start:start + size])
                       for start in range(0, len(symbols), size))
    with ThreadPoolExecutor(max_workers=6) as pool:
        return {symbol: result for batch in pool.map(fetch, batches) for symbol, result in batch.items()}
