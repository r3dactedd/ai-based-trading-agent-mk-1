CREATE TABLE IF NOT EXISTS cycles (
    id uuid PRIMARY KEY,
    mode text NOT NULL,
    slot timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status text NOT NULL,
    steps jsonb NOT NULL DEFAULT '{}',
    summary jsonb NOT NULL DEFAULT '{}',
    UNIQUE (mode, slot)
);
CREATE TABLE IF NOT EXISTS scans (
    cycle_id uuid NOT NULL REFERENCES cycles(id),
    symbol text NOT NULL,
    asset_class text NOT NULL,
    status text NOT NULL,
    details jsonb NOT NULL,
    PRIMARY KEY (cycle_id, symbol)
);
CREATE TABLE IF NOT EXISTS signals (
    cycle_id uuid NOT NULL REFERENCES cycles(id),
    symbol text NOT NULL,
    side text NOT NULL,
    strategy text NOT NULL,
    quant_score double precision NOT NULL,
    decision jsonb NOT NULL,
    conviction double precision,
    risk text NOT NULL,
    PRIMARY KEY (cycle_id, symbol)
);
CREATE TABLE IF NOT EXISTS orders (
    client_id text PRIMARY KEY,
    cycle_id uuid NOT NULL REFERENCES cycles(id),
    mode text NOT NULL,
    symbol text NOT NULL,
    asset_class text NOT NULL,
    side text NOT NULL,
    reference_price double precision NOT NULL,
    notional double precision,
    quantity double precision,
    status text NOT NULL,
    broker_id text,
    filled_qty double precision NOT NULL DEFAULT 0,
    filled_price double precision,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS portfolios (
    cycle_id uuid PRIMARY KEY REFERENCES cycles(id),
    mode text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    equity double precision NOT NULL,
    cash double precision NOT NULL,
    positions jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mode text NOT NULL,
    cycle_id uuid NOT NULL REFERENCES cycles(id),
    kind text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    data jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_mode_time ON orders(mode, created_at DESC);
CREATE INDEX IF NOT EXISTS portfolios_mode_time ON portfolios(mode, recorded_at DESC);
CREATE INDEX IF NOT EXISTS events_mode_id ON events(mode, id DESC);

CREATE TABLE IF NOT EXISTS asset_catalogs (
    mode text PRIMARY KEY,
    refreshed_at timestamptz NOT NULL,
    stock_session text NOT NULL DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS assets (
    mode text NOT NULL,
    symbol text NOT NULL,
    asset_class text NOT NULL,
    name text NOT NULL,
    exchange text,
    tradable boolean NOT NULL,
    fractionable boolean NOT NULL,
    last_scan_at timestamptz,
    scan_status text,
    scan_reason text,
    latest_ready jsonb,
    PRIMARY KEY (mode, symbol)
);
CREATE INDEX IF NOT EXISTS assets_rotation ON assets(mode, asset_class, last_scan_at NULLS FIRST, symbol);
