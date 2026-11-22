import json
import math
from collections import defaultdict
from datetime import timedelta
from statistics import fmean, pstdev

import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from config import settings


def ema(values, period):
    weight = 2 / (period + 1)
    smooth = [values[0]]
    for value in values[1:]:
        smooth.append(smooth[-1] + weight * (value - smooth[-1]))
    return smooth


def wilder(values, period):
    average = fmean(values[:period])
    for value in values[period:]:
        average = (average * (period - 1) + value) / period
    return average


def features(bars):
    close = [bar.close for bar in bars]
    delta = [b - a for a, b in zip(close, close[1:])]
    gain = wilder([max(d, 0) for d in delta], 14)
    loss = wilder([max(-d, 0) for d in delta], 14)
    rsi = 100 * gain / (gain + loss) if gain + loss else 50
    macd = [a - b for a, b in zip(ema(close, 12), ema(close, 26))]
    signal = ema(macd, 9)[-1]
    true_ranges = [max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
                   for a, b in zip(bars, bars[1:])]
    mid = fmean(close[-20:])
    spread = 2 * pstdev(close[-20:])
    reference_volume = fmean([b.volume for b in bars[-21:-1]])
    hourly = defaultdict(list)
    for bar in bars:
        hourly[bar.time.replace(minute=0, second=0, microsecond=0)].append(bar)
    hourly_closes = [group[-1].close for group in hourly.values() if len(group) == 12]
    if len(hourly_closes) < 26:
        raise ValueError("Insufficient complete hourly bars")
    return {"price": close[-1], "ema_12": ema(close, 12)[-1], "ema_26": ema(close, 26)[-1],
            "rsi": rsi, "macd": macd[-1], "macd_signal": signal, "macd_hist": macd[-1] - signal,
            "atr": wilder(true_ranges, 14), "bb_lower": mid - spread,
            "bb_middle": mid, "bb_upper": mid + spread,
            "volume": bars[-1].volume, "volume_avg": reference_volume,
            "relative_volume": bars[-1].volume / reference_volume if reference_volume else 0,
            "hourly_ema_12": ema(hourly_closes, 12)[-1],
            "hourly_ema_26": ema(hourly_closes, 26)[-1],
            "return_1h": close[-1] / close[-13] - 1
            if bars[-1].time - bars[-13].time == timedelta(hours=1) else None,
            "previous_high": max(b.high for b in bars[-21:-1])}


def candidates(indicators, positions):
    output = []
    for symbol, f in indicators.items():
        price = f["price"]
        position = positions.get(symbol)
        if position:
            if position["qty"] > 0 and price <= position["avg_entry"] - 2 * f["atr"]:
                output.append({"symbol": symbol, "side": "sell", "strategy": "ATR exit",
                               "quant_score": 100})
            elif position["qty"] > 0 and price < f["ema_26"] and f["rsi"] < 42:
                output.append({"symbol": symbol, "side": "sell", "strategy": "Trend exit",
                               "quant_score": 92})
            continue

        rising = f["ema_12"] > f["ema_26"] and f["hourly_ema_12"] > f["hourly_ema_26"]
        momentum = f["macd_hist"] > 0 and 48 <= f["rsi"] <= 75
        liquid = f["relative_volume"] >= .65 and f["atr"] / price <= .035
        if rising and momentum and liquid and price > f["ema_12"]:
            breakout = price >= f["previous_high"] and f["relative_volume"] >= 1.1
            score = 78 + min(12, f["relative_volume"] * 4) + (6 if breakout else 0)
            output.append({"symbol": symbol, "side": "buy",
                           "strategy": "Volume breakout" if breakout else "Trend continuation",
                           "quant_score": min(100, score)})
    return output


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    symbol: str
    action: Literal["approve", "reject"]
    score: int = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=300)


class Decisions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decisions: list[Decision]


def evaluate(client, signals, indicators, mode_context):
    if not signals:
        return {}
    if not settings.uses_openai:
        return {row["symbol"]: {"action": "approve", "score": min(95, round(row["quant_score"])),
                                "reason": "Demo evaluation from quantitative inputs.",
                                "source": "demo"} for row in signals}

    data = [{"candidate": row, "features": indicators[row["symbol"]]} for row in signals]
    response = client.post("https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {settings.ai_key}"},
                           json={"model": settings.model, "temperature": 0,
                                 "response_format": {"type": "json_schema", "json_schema": {
                                     "name": "trade_evaluations", "strict": True,
                                     "schema": Decisions.model_json_schema()}},
                                 "messages": [
                                     {"role": "system", "content": (
                                         "Evaluate each candidate in this paper trading system. "
                                         "Return exactly one assessment per symbol. Score confidence "
                                         "in the proposed side from 0 to 100 using the supplied market "
                                         "context and feature alignment. Reject weak or conflicting "
                                         "evidence. Keep each reason short. Never size or place trades.")},
                                     {"role": "user", "content": json.dumps(
                                         {"market_context": mode_context, "candidates": data})}]},
                           timeout=35)
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    validated = Decisions.model_validate_json(message["content"])
    expected = {row["symbol"] for row in signals}
    if len(validated.decisions) != len(expected) or {d.symbol for d in validated.decisions} != expected:
        raise ValueError("AI output did not cover the candidate set")
    return {d.symbol: {**d.model_dump(exclude={"symbol"}), "source": settings.model}
            for d in validated.decisions}


def return_samples(bars):
    return {b.time: math.log(b.close / a.close) for a, b in zip(bars, bars[1:])
            if b.time - a.time == timedelta(minutes=5)}


def correlation(left, right):
    times = sorted(left.keys() & right.keys())[-96:]
    if len(times) < 30:
        return None
    a, b = [left[t] for t in times], [right[t] for t in times]
    mean_a, mean_b = fmean(a), fmean(b)
    scale = pstdev(a) * pstdev(b)
    return fmean((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / scale if scale else None
