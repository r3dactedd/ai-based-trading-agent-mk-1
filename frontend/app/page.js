"use client";

import { useEffect, useRef, useState } from "react";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const clock = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Bangkok",
});
const localDate = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Bangkok",
});
const priceNumber = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 8 });
const quoteOf = (symbol) => symbol?.split("/")[1] || "USD";
const dateTime = (value) => value ? `${localDate.format(new Date(value))} ${time(value).slice(0, 5)}` : "—";
const time = (value) => value ? clock.format(new Date(value)) : "—";
const amount = (value, quote = "USD") => {
  if (value == null) return "—";
  return quote === "USD" && (value === 0 || Math.abs(value) >= .01)
    ? money.format(value) : `${priceNumber.format(value)} ${quote}`;
};
const title = (value) => String(value || "pending").replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
const pct = (value) => value == null ? "—" : `${(value * 100).toFixed(2)}%`;
const stages = [
  ["initialize", "Initialize cycle"], ["scan", "Market scanning"], ["indicators", "Calculate indicators"],
  ["signals", "Generate signals"], ["ai", "AI evaluation"], ["conviction", "Conviction scoring"],
  ["risk", "Risk validation"], ["execution", "Paper execution"], ["reconciliation", "Portfolio reconciliation"],
  ["logging", "Logging & notifications"], ["dashboard", "Dashboard update"],
];

function Icon({ name }) {
  const icons = { overview: "◈", markets: "⌁", signals: "✦", orders: "⇄", cycles: "◴" };
  return <span className="icon" aria-hidden>{icons[name]}</span>;
}

function Chart({ points, label, quote }) {
  const canvas = useRef(null);
  useEffect(() => {
    const node = canvas.current;
    if (!node || !points.length) return;
    const draw = () => {
      const { width, height } = node.getBoundingClientRect();
      if (!width || !height) return;
      const ratio = window.devicePixelRatio || 1;
      node.width = Math.round(width * ratio);
      node.height = Math.round(height * ratio);
      const ctx = node.getContext("2d");
      ctx.scale(ratio, ratio);
      const values = points.map((point) => point[1]);
      const low = Math.min(...values);
      const high = Math.max(...values);
      const spread = high - low || Math.max(high * 0.004, Number.EPSILON);
      const min = low - spread * .13;
      const max = high + spread * .13;
      const left = width < 420 ? 49 : 61;
      const right = width - 12;
      const top = 14;
      const bottom = height - 26;
      const first = new Date(points[0][0]).getTime();
      const last = new Date(points.at(-1)[0]).getTime();
      const gx = (i) => last === first ? (left + right) / 2
        : left + (right - left) * (new Date(points[i][0]).getTime() - first) / (last - first);
      const gy = (n) => bottom - (bottom - top) * (n - min) / (max - min);
      ctx.font = "11px ui-monospace, monospace";
      ctx.textBaseline = "middle";
      ctx.textAlign = "right";
      for (let i = 0; i < 5; i++) {
        const y = top + (bottom - top) * i / 4;
        ctx.strokeStyle = "#28303b";
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
        ctx.fillStyle = "#9aa8b9";
        const tick = max - (max - min) * i / 4;
        const formatted = Math.abs(tick) >= 1000 ? compact.format(tick)
          : Math.abs(tick) < .01 && tick !== 0 ? tick.toExponential(2) : tick.toFixed(2);
        ctx.fillText(quote === "USD" ? `$${formatted}` : formatted, left - 8, y);
      }
      ctx.textAlign = "center";
      const ticks = points.length === 1 ? [0] : [...new Set([0, .25, .5, .75, 1]
        .map((part) => Math.round(part * (points.length - 1))))];
      for (const i of ticks) {
        const x = Math.max(left, Math.min(right - 14, gx(i)));
        ctx.fillText(time(points[i][0]).slice(0, 5), x, height - 9);
      }
      if (points.length > 1) {
        const gradient = ctx.createLinearGradient(0, top, 0, bottom);
        gradient.addColorStop(0, "rgba(88, 221, 185, .29)");
        gradient.addColorStop(1, "rgba(88, 221, 185, .015)");
        ctx.beginPath();
        values.forEach((v, i) => i ? ctx.lineTo(gx(i), gy(v)) : ctx.moveTo(gx(i), gy(v)));
        ctx.lineTo(gx(values.length - 1), bottom);
        ctx.lineTo(gx(0), bottom);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();
        ctx.beginPath();
        values.forEach((v, i) => i ? ctx.lineTo(gx(i), gy(v)) : ctx.moveTo(gx(i), gy(v)));
        ctx.strokeStyle = "#65e6bf";
        ctx.lineWidth = 2;
        ctx.stroke();
      }
      ctx.fillStyle = "#83f1d0";
      ctx.beginPath();
      ctx.arc(gx(values.length - 1), gy(values.at(-1)), 3.5, 0, 2 * Math.PI);
      ctx.fill();
    };
    const observer = new ResizeObserver(draw);
    observer.observe(node);
    draw();
    return () => observer.disconnect();
  }, [points, quote]);
  if (!points.length) return <div className="chart-empty">Chart data appears after a market scan or portfolio reconciliation.</div>;
  return <canvas className="chart" ref={canvas} role="img"
    aria-label={`${label}. ${points.length} observations. Latest value ${amount(points.at(-1)[1], quote)}.`} />;
}

function MarketTable({ scans, selected, onSelect, full = false, empty = "Waiting for the first market scan." }) {
  return <div className="table-wrap"><table><thead><tr>
    <th>Asset</th><th>Market</th><th className="numeric">Price</th><th className="numeric">1h change</th>
    {full && <><th className="numeric">RSI</th><th className="numeric">Relative volume</th></>}
    {full && <th>Last bar (UTC+7)</th>}<th>Feed</th>
  </tr></thead><tbody>
    {scans.map((row) => {
      const f = row.details.features || {};
      return <tr key={row.symbol} className={row.symbol === selected ? "selected-row" : ""}>
        <td><button className="symbol" onClick={() => onSelect(row.symbol)}
          aria-pressed={row.symbol === selected}>{row.symbol}</button>
          {full && row.name !== row.symbol && <small className="cell-note">{row.name}</small>}</td>
        <td>{row.asset_class === "stock" ? "US equity" : "Crypto"}</td>
        <td className="numeric">{amount(f.price, quoteOf(row.symbol))}</td>
        <td className={`numeric ${f.return_1h > 0 ? "positive" : f.return_1h < 0 ? "negative" : ""}`}>
          {f.return_1h > 0 ? "+" : ""}{pct(f.return_1h)}</td>
        {full && <><td className="numeric">{f.rsi?.toFixed(1) ?? "—"}</td>
          <td className="numeric">{f.relative_volume == null ? "—" : `${f.relative_volume.toFixed(2)}×`}</td></>}
        {full && <td>{dateTime(row.details.bar_time)}</td>}
        <td><span className={`pill ${row.status}`} title={row.details.reason || "Latest completed bar validated"}>{title(row.status)}</span></td>
      </tr>;
    })}
    {!scans.length && <tr><td colSpan={full ? 8 : 5} className="empty">{empty}</td></tr>}
  </tbody></table></div>;
}

function Cycle({ cycle, data, next }) {
  return <section className="panel cycle">
    <header className="panel-head"><div><h2>Cycle pipeline</h2>
      <p>{cycle ? `${cycle.id.slice(0, 8)} · ${dateTime(cycle.slot)} UTC+7` : "Waiting for the worker"}</p></div>
      <span className={`pill ${cycle?.status || ""}`}>{title(cycle?.status || "Waiting")}</span></header>
    <ol className="pipeline">{stages.map(([key, name]) => {
      const state = cycle?.steps?.[key];
      return <li key={key}><span className={`stage-dot ${state?.state || ""}`} aria-hidden>
        {state?.state === "done" ? "✓" : state ? "!" : "·"}</span>
        <div><div>{name}</div>{state?.detail && <small>{state.detail}</small>}</div>
      </li>;
    })}</ol>
    <div className="cycle-foot"><span>Next scheduled cycle</span><strong>{next ? `${time(next)} UTC+7` : "—"}</strong></div>
    {data && <div className="cycle-foot"><span>AI evaluator</span><strong>{data.ai}</strong></div>}
  </section>;
}

function Signals({ signals }) {
  return <section className="panel"><header className="panel-head"><div><h2>Signal decisions</h2>
    <p>Quantitative candidates and evaluations from the latest cycle</p></div></header>
    <div className="table-wrap"><table><thead><tr><th>Asset</th><th>Strategy</th><th>Side</th>
      <th className="numeric">Quant</th><th className="numeric">AI</th><th className="numeric">Conviction</th><th>Risk decision</th></tr></thead>
      <tbody>{signals.map((row) => <tr key={row.symbol}><td className="mono">{row.symbol}</td><td>{row.strategy}</td>
        <td><span className={`pill ${row.side}`}>{title(row.side)}</span></td>
        <td className="numeric">{row.quant_score.toFixed(1)}</td><td className="numeric">{row.decision.score ?? "—"}</td>
        <td className="numeric">{row.conviction ?? "—"}</td><td>{row.risk}
          {row.decision.reason && <small className="cell-note">{row.decision.reason}</small>}</td></tr>)}
        {!signals.length && <tr><td colSpan="7" className="empty">No quantitative candidates in the latest cycle.</td></tr>}
      </tbody></table></div></section>;
}

function Orders({ orders, positions }) {
  return <div className="stack"><section className="panel"><header className="panel-head"><div><h2>Open positions</h2>
    <p>From the latest reconciled portfolio</p></div></header>
    <div className="table-wrap"><table><thead><tr><th>Asset</th><th>Market</th><th className="numeric">Quantity</th>
      <th className="numeric">Average entry</th><th className="numeric">Market value</th></tr></thead>
      <tbody>{Object.entries(positions).map(([symbol, row]) => <tr key={symbol}><td className="mono">{symbol}</td>
        <td>{title(row.asset_class)}</td><td className="numeric">{row.qty.toFixed(6)}</td>
        <td className="numeric">{amount(row.avg_entry)}</td><td className="numeric">{amount(row.market_value)}</td></tr>)}
        {!Object.keys(positions).length && <tr><td colSpan="5" className="empty">No open positions.</td></tr>}
      </tbody></table></div></section>
    <section className="panel"><header className="panel-head"><div><h2>Order history</h2><p>Latest 40 agent orders</p></div></header>
      <div className="table-wrap"><table><thead><tr><th>Asset</th><th>Side</th><th>Status</th><th className="numeric">Order value</th>
        <th className="numeric">Filled quantity</th><th className="numeric">Fill price</th><th>Created (UTC+7)</th></tr></thead>
        <tbody>{orders.map((row) => <tr key={row.client_id}><td className="mono" title={row.client_id}>{row.symbol}</td>
          <td><span className={`pill ${row.side}`}>{title(row.side)}</span></td>
          <td><span className={`pill ${row.status}`}>{title(row.status)}</span></td>
          <td className="numeric">{amount(row.notional ?? row.quantity * row.reference_price)}</td>
          <td className="numeric">{row.filled_qty.toFixed(6)}</td><td className="numeric">{amount(row.filled_price)}</td>
          <td>{dateTime(row.created_at)}</td></tr>)}
          {!orders.length && <tr><td colSpan="7" className="empty">No paper orders have been submitted yet.</td></tr>}
        </tbody></table></div></section></div>;
}

export default function Dashboard() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("Connecting");
  const [section, setSection] = useState("overview");
  const [selected, setSelected] = useState("");
  const [chartMode, setChartMode] = useState("market");
  const [range, setRange] = useState("4H");
  const [cycleId, setCycleId] = useState("");
  const [wallTime, setWallTime] = useState(0);

  useEffect(() => {
    let active = true;
    let socket, retry, controller;
    let loading = false, queued = false;
    async function refresh() {
      if (!active) return;
      if (loading) { queued = true; return; }
      loading = true;
      controller = new AbortController();
      try {
        const result = await fetch("/api/dashboard", { cache: "no-store", signal: controller.signal });
        if (!result.ok) throw new Error("Dashboard data is temporarily unavailable.");
        const snapshot = await result.json();
        if (active) { setData(snapshot); setError(""); }
      } catch {
        if (active) setError("Dashboard data is temporarily unavailable. Retrying automatically.");
      } finally {
        loading = false;
        if (queued) { queued = false; refresh(); }
      }
    }
    function connect() {
      if (!active) return;
      socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live`);
      socket.onopen = () => { if (active) setConnection("Live"); };
      socket.onmessage = refresh;
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!active) return;
        setConnection("Polling");
        retry = setTimeout(connect, 5000);
      };
    }
    setWallTime(Date.now());
    refresh();
    connect();
    const poll = setInterval(refresh, 15000);
    const tick = setInterval(() => setWallTime(Date.now()), 1000);
    return () => {
      active = false;
      controller?.abort();
      socket?.close();
      clearTimeout(retry);
      clearInterval(poll);
      clearInterval(tick);
    };
  }, []);

  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [page, setPage] = useState(1);
  const [marketPage, setMarketPage] = useState(null);
  const [selectedAsset, setSelectedAsset] = useState(null);
  const [marketError, setMarketError] = useState("");
  const marketVersion = data?.server_time;

  useEffect(() => {
    if (section !== "markets") return;
    const params = new URLSearchParams({ q: query, kind, page: String(page) });
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/markets?${params}`, { signal: controller.signal });
        if (!response.ok) throw new Error("Market catalog unavailable");
        const result = await response.json();
        setMarketPage({ ...result, query, kind, requestedPage: page });
        setMarketError("");
      } catch {
        if (!controller.signal.aborted) setMarketError("Market catalog is temporarily unavailable.");
      }
    }, 140);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [section, query, kind, page, marketVersion]);

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ symbol: selected });
    async function refreshSelected() {
      try {
        const response = await fetch(`/api/markets?${params}`, { signal: controller.signal });
        if (!response.ok) return;
        setSelectedAsset(await response.json());
      } catch {
        // The dashboard will retry on its next refresh.
      }
    }
    refreshSelected();
    return () => controller.abort();
  }, [selected, marketVersion]);

  const activePage = marketPage?.query === query && marketPage.kind === kind
    && marketPage.requestedPage === page ? marketPage : null;
  const scans = data?.scans || [];
  const cycles = data?.cycles || [];
  const latest = cycles[0];
  const cycle = cycles.find((row) => row.id === cycleId) || latest;
  const positions = data?.portfolio?.positions || {};
  const preferred = selected
    ? (selectedAsset?.symbol === selected ? selectedAsset : null)
      || scans.find((row) => row.symbol === selected)
      || activePage?.items.find((row) => row.symbol === selected)
    : scans.find((row) => row.status === "ready") || scans[0];
  const quote = chartMode === "market" ? quoteOf(selected || preferred?.symbol) : "USD";
  const allPoints = chartMode === "market" ? preferred?.details.chart || [] : data?.equity || [];
  const hours = { "1H": 1, "4H": 4, "24H": 24 };
  const cutoff = new Date(allPoints.at(-1)?.[0] || 0).getTime() - hours[range] * 3600000;
  const points = allPoints.filter(([date]) => new Date(date).getTime() >= cutoff);
  const value = points.at(-1)?.[1];
  const change = points.length > 1 ? value / points[0][1] - 1 : null;
  const next = data && wallTime ? (Math.floor((wallTime - 5000) / 300000) + 1) * 300000 + 5000 : null;
  const drawdown = data?.portfolio && data.peak_equity > 0
    ? 1 - data.portfolio.equity / data.peak_equity : null;
  const metrics = [
    ["Portfolio equity", amount(data?.portfolio?.equity), data?.mode === "alpaca" ? "Alpaca paper account" : "Simulated paper account"],
    ["Available cash", amount(data?.portfolio?.cash), "Latest reconciled balance"],
    ["Open positions", data?.portfolio ? Object.keys(positions).length : "—",
      `${cycles.find((row) => row.finished_at)?.summary.ready ?? 0} feeds validated last cycle`],
    ["Drawdown", pct(drawdown), "From recorded equity peak"],
  ];

  function select(symbol) {
    setSelected(symbol);
    setChartMode("market");
  }

  const chart = <section className="panel chart-panel"><header className="panel-head chart-head">
    <div><h2>{chartMode === "market" ? selected || preferred?.symbol || "Market price" : "Portfolio equity"}</h2>
      <p>{chartMode === "market" ? `Completed 5-minute bars · ${quote} · UTC+7` : "Reconciled equity · USD · UTC+7"}</p>
      {chartMode === "market" && preferred && preferred.status !== "ready" && preferred.details.bar_time &&
        <p>Last available bar {dateTime(preferred.details.bar_time)} UTC+7</p>}</div>
    <div className="segments" aria-label="Chart series">{[["market", "Market"], ["equity", "Equity"]].map(([key, name]) =>
      <button key={key} aria-pressed={chartMode === key} onClick={() => setChartMode(key)}>{name}</button>)}</div></header>
    <div className="chart-toolbar"><div><strong>{amount(value, quote)}</strong>
      {change != null && <span className={change >= 0 ? "positive" : "negative"}>{change >= 0 ? "+" : ""}{pct(change)}</span>}</div>
      <div className="segments" aria-label="Chart time range">{Object.keys(hours).map((key) =>
        <button key={key} aria-pressed={range === key} onClick={() => setRange(key)}>{key}</button>)}</div></div>
    <Chart points={points} quote={quote} label={chartMode === "market" ? selected || preferred?.symbol || "Market price" : "Portfolio equity"} />
  </section>;

  return <div className="app-shell"><aside className="rail">
    <div className="brand"><div className="brand-icon" aria-hidden>◈</div><div>Meridian<small>Paper trading</small></div></div>
    <nav aria-label="Dashboard sections">{["overview", "markets", "signals", "orders", "cycles"].map((name) =>
      <button key={name} aria-current={section === name ? "page" : undefined} onClick={() => setSection(name)}>
        <Icon name={name} />{title(name)}</button>)}</nav>
    <div className="rail-footer"><span className="badge-dot" /> Paper execution</div>
  </aside><main>
    <header className="top"><div><h1>{title(section)}</h1><p>{section === "overview"
      ? "Automated US equity and crypto monitoring" : {
        markets: "Validated prices and indicators", signals: "Candidate evaluations and risk outcomes",
        orders: "Paper executions and reconciled holdings", cycles: "Five-minute worker activity",
      }[section]}</p></div>
      <div className="top-status"><span className="pill mode">{data?.mode === "alpaca" ? "Alpaca paper" : data ? "Simulation" : "Paper trading"}</span>
        <span className={`pill ${connection === "Live" ? "ready" : ""}`}><span className="badge-dot" />{connection}</span></div>
    </header>
    {error && <p role="status" className="error">{error}</p>}
    {section === "overview" && <>
      <div className="metrics">{metrics.map(([label, metric, note]) => <section className="metric" key={label}>
        <h2>{label}</h2><strong>{metric}</strong><small>{note}</small></section>)}</div>
      <div className="overview"><div className="stack">{chart}<section className="panel">
        <header className="panel-head"><div><h2>Market watch</h2><p>{`${data?.catalog?.total?.toLocaleString() ?? "—"} assets in the catalog`}</p></div>
          <button className="outline" onClick={() => setSection("markets")}>View markets</button></header>
        <MarketTable scans={scans} selected={preferred?.symbol} onSelect={select} /></section></div>
        <Cycle cycle={latest} data={data} next={next} /></div>
    </>}
    {section === "markets" && <div className="stack">{chart}<section className="panel">
      <header className="panel-head"><div><h2>Asset catalog</h2>
        <p>{data?.catalog ? `${data.catalog.stocks.toLocaleString()} US equities · ${data.catalog.crypto.toLocaleString()} crypto pairs` : "Waiting for asset discovery"}</p>
      </div></header>
      <div className="catalog-controls">
        <label>Search <input type="search" placeholder="Symbol or name" value={query} maxLength={80}
          onChange={(event) => { setQuery(event.target.value); setPage(1); }} /></label>
        <label>Market <select value={kind} onChange={(event) => { setKind(event.target.value); setPage(1); }}>
          <option value="">All markets</option><option value="stock">US equities</option><option value="crypto">Crypto</option>
        </select></label>
      </div>
      <MarketTable scans={activePage?.items || []} selected={selected || preferred?.symbol} onSelect={select} full
        empty={marketError || (activePage ? "No matching assets." : "Loading assets…")} />
      {activePage?.total > 0 && <nav className="catalog-pagination" aria-label="Asset catalog pages">
        <button className="outline" disabled={activePage.page <= 1} onClick={() => setPage(activePage.page - 1)}>Previous</button>
        <span aria-live="polite">{((activePage.page - 1) * activePage.page_size + 1).toLocaleString()}–{
          Math.min(activePage.page * activePage.page_size, activePage.total).toLocaleString()} of {activePage.total.toLocaleString()}</span>
        <button className="outline" disabled={activePage.page * activePage.page_size >= activePage.total}
          onClick={() => setPage(activePage.page + 1)}>Next</button>
      </nav>}
    </section></div>}
    {section === "signals" && <Signals signals={data?.signals || []} />}
    {section === "orders" && <Orders orders={data?.orders || []} positions={positions} />}
    {section === "cycles" && <div className="overview"><div className="stack"><section className="panel">
      <header className="panel-head"><div><h2>Cycle history</h2><p>Latest 20 scheduled runs</p></div></header>
      <div className="table-wrap"><table><thead><tr><th>Cycle</th><th>Slot (UTC+7)</th><th>Status</th><th className="numeric">Orders</th></tr></thead>
        <tbody>{cycles.map((row) => <tr key={row.id} className={row.id === cycle?.id ? "selected-row" : ""}>
          <td><button className="symbol" aria-pressed={row.id === cycle?.id} onClick={() => setCycleId(row.id)}>{row.id.slice(0, 8)}</button></td>
          <td>{dateTime(row.slot)}</td>
          <td><span className={`pill ${row.status}`}>{title(row.status)}</span></td><td className="numeric">{row.summary.orders ?? "—"}</td></tr>)}
          {!cycles.length && <tr><td colSpan="4" className="empty">No cycles recorded yet.</td></tr>}
        </tbody></table></div></section>
      <section className="panel"><header className="panel-head"><div><h2>Recent activity</h2><p>Latest worker events</p></div></header>
        <ul className="activity">{(data?.events || []).map((item) => <li key={item.id}>
          <time>{time(item.created_at)} UTC+7</time><span>{title(item.data.step || item.kind)}</span>
          <small>{item.data.detail || item.data.error ||
            [item.data.symbol, item.data.status].filter(Boolean).map(title).join(" · ")}</small></li>)}
          {!data?.events.length && <li className="empty">No events recorded yet.</li>}</ul></section></div>
      <Cycle cycle={cycle} data={data} next={next} /></div>}
  </main></div>;
}
