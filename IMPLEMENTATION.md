# Implementation list

## Project setup

- Created a Python worker, FastAPI API, PostgreSQL database, and Next.js dashboard.
- Added `compose.yaml` and backend and frontend Dockerfiles. Compose starts the four services, waits for PostgreSQL, restarts services, and preserves the database in a named volume.
- Added `.env.example` for Alpaca paper credentials, OpenAI, Telegram, market symbols, risk limits, and dashboard origins. The stack starts in credential-free simulation mode.
- Added `README.md` with startup, integration setup, risk rules, and operational commands.

## Five-minute trading cycle

| Stage | Implementation | Main files |
| --- | --- | --- |
| 1. Initialize | Schedule a run every 300 seconds, allow a five-second data publication grace period, assign a UUID, and acquire a PostgreSQL advisory lock. Enforce one cycle per mode and time slot. | `backend/worker.py`, `backend/db.py`, `backend/schema.sql` |
| 2. Scan markets | Fetch completed five-minute bars for US equities and USD crypto pairs. Check the equity calendar, freshness, recent continuity, and OHLCV values. Record closed and unavailable markets. | `backend/market.py`, `backend/broker.py` |
| 3. Calculate indicators | Compute EMA, RSI, MACD, ATR, Bollinger Bands, relative volume, complete hourly EMA features, and timestamp-aligned returns. | `backend/strategy.py` |
| 4. Generate candidates | Use trend continuation and volume-breakout rules for long entries. Generate exits for held positions using ATR and trend rules. | `backend/strategy.py` |
| 5. Evaluate with AI | Send quantitative features and market context to OpenAI. Validate its approval, score, and reason for every candidate. Provide a deterministic evaluator for credential-free simulation. | `backend/strategy.py`, `backend/config.py` |
| 6. Score conviction | Weight quantitative and AI scores 65/35. Gate entries on model approval and configurable score thresholds. Persist both scores and the decision. | `backend/worker.py`, `backend/execution.py` |
| 7. Check risk | Enforce cash, position size, symbol exposure, gross exposure, crypto exposure, drawdown, existing positions, pending orders, cooldown, and correlation limits. | `backend/execution.py`, `backend/config.py` |
| 8. Execute paper orders | Persist order intent and client ID before sending. Use the Alpaca paper order endpoint or the simulator. Check the equity session again before submission. | `backend/execution.py`, `backend/broker.py` |
| 9. Reconcile | Retrieve order statuses by client ID, reserve exposure for unresolved orders, and record cash, equity, and positions. Flag cycles interrupted by a worker restart. | `backend/worker.py`, `backend/execution.py`, `backend/broker.py` |
| 10. Log and notify | Persist stage results, scans, signals, decisions, orders, snapshots, and errors. Send an optional Telegram summary each cycle and record delivery failures. | `backend/worker.py`, `backend/db.py`, `backend/schema.sql` |
| 11. Update the dashboard | Publish PostgreSQL-backed cycle events through FastAPI WebSocket. Refresh the Next.js dashboard on incoming events, reconnect automatically, and poll as a fallback. | `backend/api.py`, `frontend/next.config.mjs`, `frontend/app/` |

## Dashboard

- Created Overview, Markets, Signals, Orders, and Cycles views.
- Added market and portfolio equity charts, selectable assets and time ranges, and cash, equity, position, and drawdown summaries.
- Added per-cycle pipeline status, risk decisions, reconciled holdings, and paper order history.
- Added a read-only dashboard API and a health endpoint. The dashboard and API bind to localhost by default.

## Dynamic asset discovery and local time

- Discover active US equities and crypto spot pairs from Alpaca and persist the catalog independently of scan results.
- Rotate market scans in batches, prioritize held assets, and keep the most recent validated chart available for each asset.
- Added a searchable, paginated Markets view, a market filter, and last-bar timestamps. Non-USD pairs are displayed in their quote currency and skipped for automated orders.
- Display dashboard, chart, and Telegram times in Bangkok time (UTC+7). Scheduling and stored timestamps still use UTC.

## Verification

- Ran the Python syntax check, validated the Compose configuration, and built Next.js for production. The final npm audit reported zero known vulnerabilities.
- Verified that simulated cycles complete all eleven stages. A separate PostgreSQL test verified the advisory lock, one-cycle-per-slot constraint, a simulated fill, persisted AI and risk decisions, and the reconciled cash and position.
- Verified stock sell and crypto buy payloads against mocked Alpaca paper responses, including lookup by client order ID. Checked stale-bar rejection, indicator output, and the structured OpenAI response using a mock.
- Verified the running Next.js page and dashboard API. Received an automatically scheduled cycle's WebSocket event through the production Next.js proxy. The managed browser preview relay timed out during startup.
- Tested catalog discovery, batching and pagination, rotation, search, retained charts, and the USD-only paper-order gate with mocked Alpaca responses and PostgreSQL.
- Live Alpaca, OpenAI, and Telegram connections require account credentials and remain unverified in the development environment.
