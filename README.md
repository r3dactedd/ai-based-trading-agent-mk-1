# ai-based-trading-agent-mk-1
Personal project where I want to try and create an AI-Based Trading Agent for Crypto and US Market

Meridian runs a five-minute trading loop for US equities and USD crypto pairs. The stack has a Python worker, a FastAPI read API, PostgreSQL, and a Next.js dashboard. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the stage-by-stage implementation and verification.

## Start locally

From this directory, with Docker and Docker Compose v2.24 or later installed:

```bash
docker compose up -d --build
docker compose logs -f worker
```

Open the dashboard at http://localhost:3000. The API and its interactive documentation are at http://localhost:8000/docs. The first cycle normally runs on startup, after the five-second bar publication grace period. The worker then runs automatically every five minutes while the host and Docker are running. Demo mode simulates prices and fills, starts with $100,000, and works without keys. Its synthetic stock feed also runs outside US trading hours.

## Connect Alpaca paper trading

Copy `.env.example` to `.env` and enter your credentials in that local file. Set `TRADING_MODE=alpaca`, `ALPACA_KEY`, `ALPACA_SECRET`, and `OPENAI_API_KEY`. Keep `AI_MODE=auto` or use `AI_MODE=openai`. The stock feed defaults to IEX. Use `sip` if your Alpaca subscription provides real-time SIP access. Configure fractionable US equities and supported Alpaca USD crypto pairs in the symbol lists. The adapter uses the Alpaca paper hostname for orders.

For Telegram, set both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Start a direct conversation with the bot or add it to the target chat before starting the worker.

```bash
docker compose up -d --build
```

Demo and Alpaca records are stored separately. To return to demo, change `TRADING_MODE` to `demo` and run the same command.

## Trading and recovery behavior

- The worker acquires a PostgreSQL advisory lock and records one cycle per mode and UTC five-minute slot. Stocks trade during the exchange's regular session in Alpaca mode. The scanner ignores incomplete candles and excludes feeds that fail validation.
- The strategy computes EMA, RSI, MACD, ATR, Bollinger Bands, relative volume, and complete hourly EMA features. Entries require aligned trends, momentum, and liquidity. Candidates use either trend continuation or a volume breakout. Positions can exit on the ATR or trend rule.
- OpenAI evaluates each candidate using a structured response. An entry requires AI approval, the minimum AI score, and a conviction of at least 70 by default. Conviction weights the quantitative score at 65% and the AI score at 35%. Protective exits use the quantitative rule and must pass deterministic risk checks.
- The worker sizes long entries using cash, portfolio equity, and the symbol, gross, and crypto exposure limits in `.env.example`. It checks existing positions, pending orders, drawdown, cooldown, and absolute return correlation. Entries pause when a required limit or input fails. It blocks new entries if the model is unavailable and continues evaluating eligible protective exits.
- Before a submission, the worker commits its order intent and an Alpaca client order ID. When a result is uncertain, upcoming cycles query Alpaca by that ID and block fresh orders for the affected symbol until the order is resolved. An unresolved order that Alpaca repeatedly cannot find can require broker-side verification before releasing the reservation.
- PostgreSQL holds cycle steps, per-symbol feed results, model decisions, risk outcomes, orders, portfolios, and events. The dashboard refreshes over WebSocket and falls back to polling if the connection drops.
- Docker binds the dashboard and read API to loopback. An always-on deployment needs a running host with a persistent PostgreSQL volume. To view it remotely, use a private connection or an authenticated reverse proxy and set `DASHBOARD_ORIGINS` to the exact dashboard origin if it differs from localhost.

`docker compose stop worker` pauses new cycles. Orders already accepted by Alpaca remain subject to the broker's order lifecycle. `docker compose start worker` resumes from the current slot. `docker compose down` stops the stack and preserves its database volume.
