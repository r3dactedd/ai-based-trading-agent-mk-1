import os
from dataclasses import dataclass


def env(name, default=""):
    return os.getenv(name, default).strip()


def number(name, default, minimum, maximum):
    value = float(env(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def symbols(name, default):
    return tuple(dict.fromkeys(part.strip().upper() for part in env(name, default).split(",") if part.strip()))


@dataclass(frozen=True)
class Settings:
    database: str = env("DATABASE_URL", "postgresql://trader:trader@localhost:5432/trader")
    mode: str = env("TRADING_MODE", "demo").lower()
    alpaca_key: str = env("ALPACA_KEY")
    alpaca_secret: str = env("ALPACA_SECRET")
    stock_feed: str = env("ALPACA_STOCK_FEED", "iex")
    ai_mode: str = env("AI_MODE", "auto").lower()
    ai_key: str = env("OPENAI_API_KEY")
    model: str = env("OPENAI_MODEL", "gpt-4.1-mini")
    telegram_token: str = env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = env("TELEGRAM_CHAT_ID")
    stocks: tuple = symbols("STOCK_SYMBOLS", "SPY,QQQ,AAPL,MSFT,NVDA")
    crypto: tuple = symbols("CRYPTO_SYMBOLS", "BTC/USD,ETH/USD")
    stock_scan_limit: int = int(number("STOCK_SCAN_LIMIT", 160, 1, 300))
    crypto_scan_limit: int = int(number("CRYPTO_SCAN_LIMIT", 100, 1, 200))
    min_conviction: float = number("MIN_CONVICTION", 70, 0, 100)
    min_ai: float = number("MIN_AI_SCORE", 55, 0, 100)
    order_pct: float = number("ORDER_EQUITY_PCT", .03, .001, 1)
    symbol_pct: float = number("MAX_SYMBOL_PCT", .08, .001, 1)
    gross_pct: float = number("MAX_GROSS_PCT", .30, .001, 1)
    crypto_pct: float = number("MAX_CRYPTO_PCT", .15, .001, 1)
    drawdown_pct: float = number("MAX_DRAWDOWN_PCT", .08, .001, 1)
    correlation: float = number("MAX_CORRELATION", .85, -1, 1)
    cooldown: float = number("COOLDOWN_MINUTES", 30, 0, 1440)
    slot_seconds: int = 300

    def __post_init__(self):
        if self.mode not in {"demo", "alpaca"}:
            raise ValueError("TRADING_MODE must be demo or alpaca")
        if self.ai_mode not in {"auto", "openai", "demo"}:
            raise ValueError("AI_MODE must be auto, openai, or demo")
        if self.mode == "alpaca" and (not self.alpaca_key or not self.alpaca_secret):
            raise ValueError("Alpaca paper credentials are required in alpaca mode")
        if self.uses_openai and not self.ai_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAI evaluation")
        if self.mode == "alpaca" and not self.uses_openai:
            raise ValueError("Alpaca mode requires OpenAI evaluation")
        if bool(self.telegram_token) != bool(self.telegram_chat_id):
            raise ValueError("Set both Telegram variables to enable notifications")
        if self.mode == "demo" and not self.stocks and not self.crypto:
            raise ValueError("Configure at least one symbol")
        if self.mode == "demo" and (any("/" in symbol for symbol in self.stocks) or any(
            not symbol.endswith("/USD") for symbol in self.crypto
        )):
            raise ValueError("Use stock tickers and USD crypto pairs such as BTC/USD")

    @property
    def uses_openai(self):
        return self.ai_mode == "openai" or (
            self.ai_mode == "auto" and bool(self.ai_key or self.mode == "alpaca")
        )

    @property
    def universe(self):
        return {**dict.fromkeys(self.stocks, "stock"), **dict.fromkeys(self.crypto, "crypto")}


settings = Settings()
