(() => {
  "use strict";

  const INSTRUMENTS = ["BTCUSD", "EURUSD", "XAUUSD", "GBPUSD"];
  const STRATEGIES = [
    ["trend_following", "Trend following"],
    ["liquidity_sweep_mss", "Liquidity sweep + MSS"],
    ["breakout_retest", "Breakout retest"],
    ["range_mean_reversion", "Range mean reversion"],
  ];
  const DISPLAY_NAMES = { BTCUSD: "BTC / USD", EURUSD: "EUR / USD", XAUUSD: "XAU / USD", GBPUSD: "GBP / USD" };
  const TIMEFRAME_NAMES = { "1h": "1H", "4h": "4H", "1day": "1D" };
  const state = { instrument: "BTCUSD", timeframe: "1h", candles: [], quality: null, loading: false };
  const el = (id) => document.getElementById(id);

  function setDot(id, kind, labelId, label) {
    el(id).className = `status-dot ${kind}`;
    el(labelId).textContent = label;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
    if (!response.ok) {
      let detail = `Request failed (${response.status}).`;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
        else if (body.detail?.message) detail = body.detail.message;
      } catch (_) { /* Keep the safe generic error. */ }
      throw new Error(detail);
    }
    return response.json();
  }

  function formatPrice(code, value) {
    if (!Number.isFinite(Number(value))) return "—";
    const digits = code.endsWith("EURUSD") || code.endsWith("GBPUSD") ? 5 : 2;
    return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function formatDate(value, options = {}) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", ...options }).format(date) + " UTC";
  }

  function niceStatus(value) {
    return ({
      testing: "Testing",
      historical_pass_forward_test_required: "Paper forward test",
      approved_for_live: "Live eligible",
      suspended: "Suspended",
      historical_rejected: "Historical reject",
      insufficient_data: "Need more data",
      healthy: "Healthy",
      degraded: "Degraded",
      failed: "Failed",
      starting: "Starting",
      not_configured: "Not configured",
      ready: "Ready",
      not_sent: "Not sent",
      sent: "Sent",
      failed_delivery: "Delivery failed",
      suppressed: "Suppressed",
      completed: "Completed",
      confirmed: "Entry reached",
      developing: "Developing",
      expired: "Expired",
      invalidated: "Closed",
    })[value] || (value || "Unknown").replaceAll("_", " ");
  }

  function setMarketMeta(code, text) {
    el(`meta-${code}`).textContent = text;
  }

  async function loadMarketCard(code) {
    setMarketMeta(code, "Loading closed candles…");
    try {
      const result = await request(`/api/v1/market-data/${code}?timeframe=1h&limit=2`);
      const candles = result.candles || [];
      if (candles.length) {
        const last = candles[candles.length - 1];
        el(`price-${code}`).textContent = formatPrice(code, last.close);
        setMarketMeta(code, `${formatDate(last.timestamp, { hour: "2-digit", minute: "2-digit" })} · closed 1H`);
      } else {
        setMarketMeta(code, "No closed candles available");
      }
    } catch (error) {
      el(`price-${code}`).textContent = "—";
      setMarketMeta(code, error.message === "Market data provider is not configured" ? "Data key not configured" : "Data temporarily unavailable");
    }
  }

  function renderChart(candles) {
    const canvas = el("price-chart");
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height || !candles.length) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    const ctx = canvas.getContext("2d");
    ctx.scale(ratio, ratio);
    const width = rect.width;
    const height = rect.height;
    ctx.clearRect(0, 0, width, height);

    const visible = candles.slice(-96);
    const highs = visible.map((c) => Number(c.high));
    const lows = visible.map((c) => Number(c.low));
    const max = Math.max(...highs);
    const min = Math.min(...lows);
    const range = max - min || max * 0.002 || 1;
    const top = 8;
    const bottom = height - 23;
    const chartHeight = bottom - top;
    const right = width - 66;
    const left = 7;
    const chartWidth = right - left;
    const y = (price) => top + ((max - price) / range) * chartHeight;

    ctx.font = "9px DM Mono, Consolas, monospace";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    for (let grid = 0; grid <= 4; grid += 1) {
      const level = min + (range * grid) / 4;
      const pos = y(level);
      ctx.beginPath();
      ctx.strokeStyle = "#253238";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.moveTo(left, pos);
      ctx.lineTo(right, pos);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#718187";
      ctx.fillText(formatPrice(state.instrument, level), right + 7, pos);
    }

    const slot = chartWidth / visible.length;
    const candleWidth = Math.max(2, Math.min(9, slot * 0.58));
    visible.forEach((candle, index) => {
      const x = left + slot * (index + 0.5);
      const open = Number(candle.open);
      const close = Number(candle.close);
      const rising = close >= open;
      const color = rising ? "#64d1a0" : "#df7f7f";
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, y(Number(candle.high)));
      ctx.lineTo(x, y(Number(candle.low)));
      ctx.stroke();
      const bodyTop = Math.min(y(open), y(close));
      const bodyHeight = Math.max(1, Math.abs(y(open) - y(close)));
      ctx.globalAlpha = rising ? 0.86 : 0.78;
      ctx.fillRect(x - candleWidth / 2, bodyTop, candleWidth, bodyHeight);
      ctx.globalAlpha = 1;
    });

    const dateIndices = [0, Math.floor(visible.length / 2), visible.length - 1];
    ctx.fillStyle = "#74858a";
    ctx.textBaseline = "alphabetic";
    dateIndices.forEach((index, labelIndex) => {
      const date = new Date(visible[index].timestamp);
      const label = new Intl.DateTimeFormat(undefined, { timeZone: "UTC", month: "short", day: "2-digit", hour: state.timeframe === "1day" ? undefined : "2-digit" }).format(date);
      const x = left + slot * (index + 0.5);
      ctx.textAlign = labelIndex === 0 ? "left" : labelIndex === 1 ? "center" : "right";
      ctx.fillText(label, x, height - 5);
    });
    canvas.setAttribute("aria-label", `${DISPLAY_NAMES[state.instrument]} ${TIMEFRAME_NAMES[state.timeframe]} candlestick chart, ${visible.length} closed bars from ${visible[0].timestamp} to ${visible[visible.length - 1].timestamp}.`);
  }

  async function loadChart() {
    if (state.loading) return;
    state.loading = true;
    el("chart-message").classList.remove("hidden");
    el("chart-message").textContent = "Loading validated market data…";
    el("data-quality").textContent = "Checking candles";
    el("chart-title").textContent = DISPLAY_NAMES[state.instrument];
    try {
      const result = await request(`/api/v1/market-data/${state.instrument}?timeframe=${state.timeframe}&limit=240`);
      state.candles = result.candles || [];
      state.quality = result.quality;
      el("chart-asof").textContent = `Checked ${formatDate(result.as_of)}`;
      const quality = result.quality || {};
      if (!quality.usable) {
        el("data-quality").textContent = "Data check blocked this view";
        el("chart-message").textContent = (quality.issues || []).map((issue) => issue.message).join(" · ") || "Market data did not pass quality checks.";
        el("chart-message").classList.remove("hidden");
      } else if (!state.candles.length) {
        el("data-quality").textContent = "No completed candles";
        el("chart-message").textContent = "Waiting for the next closed candle.";
      } else {
        el("data-quality").textContent = `${quality.candle_count} candles · checks passed`;
        el("chart-message").classList.add("hidden");
        const last = state.candles[state.candles.length - 1];
        el("candle-open").textContent = formatPrice(state.instrument, last.open);
        el("candle-high").textContent = formatPrice(state.instrument, last.high);
        el("candle-low").textContent = formatPrice(state.instrument, last.low);
        el("candle-close").textContent = formatPrice(state.instrument, last.close);
        renderChart(state.candles);
      }
      const last = state.candles[state.candles.length - 1];
      if (last) {
        el(`price-${state.instrument}`).textContent = formatPrice(state.instrument, last.close);
        setMarketMeta(state.instrument, `${formatDate(last.timestamp, { hour: "2-digit", minute: "2-digit" })} · closed ${TIMEFRAME_NAMES[state.timeframe]}`);
      }
    } catch (error) {
      el("data-quality").textContent = "Data unavailable";
      el("chart-message").textContent = error.message;
      el("chart-message").classList.remove("hidden");
    } finally {
      state.loading = false;
    }
  }

  async function loadSystem() {
    try {
      const health = await request("/api/v1/system/health");
      setDot("api-dot", "ok", "api-status", "Healthy");
      const provider = health.market_data_provider || {};
      setDot("data-dot", provider.configured ? "ok" : "warn", "data-status", provider.configured ? "Configured" : "Not configured");
      const persistence = health.persistence || {};
      if (!persistence.configured) setDot("storage-dot", "warn", "storage-status", "Not linked");
      else setDot("storage-dot", persistence.connected === false ? "bad" : "ok", "storage-status", persistence.connected === false ? "Unavailable" : "Connected");
      const worker = health.worker || {};
      const workerKind = worker.status === "healthy" ? "ok" : worker.status === "degraded" || worker.status === "failed" ? "bad" : "warn";
      const workerText = worker.last_scan_at ? `${niceStatus(worker.status)} · ${formatDate(worker.last_scan_at)}` : niceStatus(worker.status || "not_configured");
      setDot("worker-dot", workerKind, "worker-status", workerText);
      const telegram = worker.telegram_status || "not_configured";
      setDot("telegram-dot", telegram === "healthy" || telegram === "ready" ? "ok" : "warn", "telegram-status", niceStatus(telegram));
      el("last-updated").textContent = `System checked ${formatDate(new Date().toISOString())}`;
    } catch (error) {
      setDot("api-dot", "bad", "api-status", "Unavailable");
      setDot("data-dot", "warn", "data-status", "Check setup");
      setDot("storage-dot", "warn", "storage-status", "Check setup");
      setDot("worker-dot", "warn", "worker-status", "Waiting for scan");
      setDot("telegram-dot", "warn", "telegram-status", "Not checked");
      el("last-updated").textContent = "Could not reach the API";
    }
  }

  function stateBadge(status) {
    const span = document.createElement("span");
    span.className = `state-badge ${status || "testing"}`;
    span.textContent = niceStatus(status || "testing");
    return span;
  }

  async function loadStrategies() {
    const body = el("strategy-rows");
    try {
      const response = await request("/api/v1/strategies/status");
      const byKey = new Map((response.items || []).map((item) => [`${item.instrument}|${item.strategy}`, item]));
      body.replaceChildren();
      INSTRUMENTS.forEach((instrument) => {
        const row = document.createElement("tr");
        const market = document.createElement("td");
        market.textContent = DISPLAY_NAMES[instrument];
        row.append(market);
        STRATEGIES.forEach(([key]) => {
          const cell = document.createElement("td");
          const entry = byKey.get(`${instrument}|${key}`);
          cell.append(stateBadge(entry?.status));
          row.append(cell);
        });
        body.append(row);
      });
    } catch (_) {
      body.innerHTML = '<tr><td colspan="5" class="table-empty">Strategy status is unavailable until the API and application state are ready.</td></tr>';
    }
  }

  function appendSignalCard(signal) {
    const card = document.createElement("article");
    card.className = "signal-card";
    const data = signal.signal_data || {};
    const header = document.createElement("div");
    header.className = "signal-card-top";
    const instrument = document.createElement("span");
    instrument.className = "signal-market";
    instrument.textContent = DISPLAY_NAMES[signal.instrument] || signal.instrument;
    const direction = document.createElement("span");
    const side = data.direction || (signal.side === "long" ? "BUY" : "SELL");
    direction.className = `direction ${String(side).toLowerCase() === "buy" ? "buy" : "sell"}`;
    direction.textContent = side;
    const status = document.createElement("span");
    status.className = "signal-state";
    status.textContent = niceStatus(signal.outcome || signal.status);
    header.append(instrument, direction, status);
    const strategy = document.createElement("p");
    strategy.className = "signal-strategy";
    strategy.textContent = `${niceStatus(signal.strategy_name)} · ${signal.mode === "live" ? "Live eligible" : "Paper forward test"}`;
    const levels = document.createElement("div");
    levels.className = "signal-levels";
    const values = [
      ["ENTRY", `${formatPrice(signal.instrument, signal.entry_zone_low)}–${formatPrice(signal.instrument, signal.entry_zone_high)}`],
      ["STOP", formatPrice(signal.instrument, signal.stop_loss)],
      ["TARGET", (signal.take_profits || []).map((price) => formatPrice(signal.instrument, price)).join(" / ") || "—"],
    ];
    values.forEach(([label, value]) => {
      const item = document.createElement("span");
      item.textContent = label;
      const strong = document.createElement("b");
      strong.textContent = value;
      item.append(strong);
      levels.append(item);
    });
    const foot = document.createElement("div");
    foot.className = "signal-foot";
    const detected = document.createElement("span");
    detected.textContent = formatDate(signal.detected_at);
    const alert = document.createElement("span");
    alert.textContent = signal.mode === "live" ? `Alert: ${niceStatus(signal.alert_status)}` : `Paper · ${niceStatus(signal.alert_status)}`;
    foot.append(detected, alert);
    card.append(header, strategy, levels, foot);
    return card;
  }

  async function loadSignals() {
    const list = el("signal-list");
    try {
      const response = await request("/api/v1/signals/recent?limit=15");
      const items = response.items || [];
      if (!items.length) {
        list.innerHTML = '<div class="empty-state"><span class="empty-icon">◌</span><strong>No signals yet</strong><p>The scanner waits for strategy-specific confirmation. No trade is better than a weak setup.</p></div>';
        return;
      }
      list.replaceChildren(...items.map(appendSignalCard));
    } catch (_) {
      list.innerHTML = '<div class="empty-state"><strong>Signal journal unavailable</strong><p>Check the Supabase connection and apply the included setup migration.</p></div>';
    }
  }

  function renderMetricsCard(title, window, passed) {
    const metrics = window?.metrics || {};
    const card = document.createElement("section");
    card.className = "result-card";
    const heading = document.createElement("h4");
    heading.textContent = `${title} · ${passed === true ? "PASS" : passed === false ? "REJECT" : "MORE DATA NEEDED"}`;
    card.append(heading);
    const values = [
      ["Trades", metrics.trade_count ?? 0],
      ["Win rate", `${Number(metrics.win_rate_percent || 0).toFixed(1)}%`],
      ["Profit factor", metrics.profit_factor == null ? "—" : Number(metrics.profit_factor).toFixed(2)],
      ["Max drawdown", `${Number(metrics.maximum_drawdown_percent || 0).toFixed(1)}%`],
    ];
    const grid = document.createElement("div");
    grid.className = "result-metrics";
    values.forEach(([label, value]) => {
      const item = document.createElement("span");
      item.textContent = label;
      const strong = document.createElement("b");
      strong.textContent = value;
      item.append(strong);
      grid.append(item);
    });
    card.append(grid);
    return card;
  }

  function renderValidation(data) {
    const result = el("validation-result");
    result.hidden = false;
    result.replaceChildren();
    const validation = data.validation;
    const passed = validation.status === "historical_pass_forward_test_required";
    const insufficient = validation.status === "insufficient_data";
    const banner = document.createElement("div");
    banner.className = "result-banner";
    const symbol = document.createElement("span");
    symbol.className = `result-symbol ${passed ? "" : insufficient ? "insufficient" : "rejected"}`;
    symbol.textContent = passed ? "✓" : insufficient ? "…" : "!";
    const text = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = passed ? "Historical gates passed" : insufficient ? "More historical evidence needed" : "Historical gates did not pass";
    const subtitle = document.createElement("p");
    subtitle.textContent = `${DISPLAY_NAMES[data.instrument]} · ${niceStatus(data.strategy)} v${data.strategy_version} · no live approval from this result`;
    text.append(title, subtitle);
    banner.append(symbol, text);
    const cards = document.createElement("div");
    cards.className = "result-grid";
    cards.append(
      renderMetricsCard("In sample", validation.in_sample, validation.in_sample?.passed),
      renderMetricsCard("Out of sample", validation.out_of_sample, validation.out_of_sample?.passed),
      renderMetricsCard("Walk forward", { metrics: { trade_count: (validation.walk_forward.windows || []).reduce((sum, item) => sum + (item.metrics?.trade_count || 0), 0), win_rate_percent: averageMetric(validation.walk_forward.windows, "win_rate_percent"), profit_factor: averageMetric(validation.walk_forward.windows, "profit_factor"), maximum_drawdown_percent: Math.max(0, ...(validation.walk_forward.windows || []).map((item) => item.metrics?.maximum_drawdown_percent || 0)) } }, validation.walk_forward.passed),
    );
    const reasons = [...(validation.in_sample?.reasons || []), ...(validation.out_of_sample?.reasons || []), ...(validation.walk_forward.windows || []).flatMap((window) => window.reasons || [])];
    const uniqueReasons = [...new Set(reasons)];
    if (uniqueReasons.length) {
      const reasonLine = document.createElement("p");
      reasonLine.className = "result-reasons";
      reasonLine.textContent = `Gate notes: ${uniqueReasons.map(niceStatus).join(" · ")}`;
      result.append(banner, cards, reasonLine);
    } else result.append(banner, cards);
    const note = document.createElement("p");
    note.className = "disclaimer";
    note.textContent = passed ? `This qualifies the strategy for paper forward testing only. Compact summary saved: ${data.validation_summary_persisted ? "yes" : "no"}. Historical results can change and never guarantee future performance.` : `No live alerts are approved. Compact summary saved: ${data.validation_summary_persisted ? "yes" : "no"}. Historical results are not a guarantee.`;
    result.append(note);
  }

  function averageMetric(windows, key) {
    const values = (windows || []).map((window) => window.metrics?.[key]).filter((value) => Number.isFinite(value));
    if (!values.length) return null;
    if (key === "maximum_drawdown_percent") return Math.max(...values);
    return values.reduce((sum, value) => sum + value, 0) / values.length;
  }

  async function runValidation(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const formData = new FormData(form);
    const payload = {
      instrument_code: String(formData.get("instrument_code")),
      strategy: String(formData.get("strategy")),
      hourly_candle_count: Number(formData.get("hourly_candle_count")),
      initial_equity: Number(formData.get("initial_equity")),
      risk_fraction: Number(formData.get("risk_percent")) / 100,
      spread_bps: Number(formData.get("spread_bps")),
      slippage_bps: Number(formData.get("slippage_bps")),
      commission_bps_per_side: Number(formData.get("commission_bps_per_side")),
      minimum_reward_risk: Number(formData.get("minimum_reward_risk")),
    };
    const button = el("validate-button");
    const result = el("validation-result");
    button.disabled = true;
    button.textContent = "Running in-memory checks…";
    result.hidden = false;
    result.replaceChildren();
    const progress = document.createElement("p");
    progress.className = "form-hint";
    progress.textContent = "Fetching closed market history and running chronological checks. This may take a little while.";
    result.append(progress);
    try {
      const data = await request("/api/v1/research/validate", { method: "POST", body: JSON.stringify(payload) });
      renderValidation(data);
      loadStrategies();
      result.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
      const box = document.createElement("div");
      box.className = "validation-error";
      box.textContent = error.message;
      result.replaceChildren(box);
    } finally {
      button.disabled = false;
      button.innerHTML = 'Run historical validation <span aria-hidden="true">→</span>';
    }
  }

  document.querySelectorAll(".market-card").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".market-card").forEach((card) => card.classList.remove("selected"));
      button.classList.add("selected");
      state.instrument = button.dataset.instrument;
      loadChart();
    });
  });
  document.querySelectorAll("[data-timeframe]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-timeframe]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.timeframe = button.dataset.timeframe;
      loadChart();
    });
  });
  el("refresh-all").addEventListener("click", () => {
    loadSystem();
    loadStrategies();
    loadSignals();
    INSTRUMENTS.forEach(loadMarketCard);
    loadChart();
  });
  el("validation-form").addEventListener("submit", runValidation);
  window.addEventListener("resize", () => renderChart(state.candles));

  loadSystem();
  loadStrategies();
  loadSignals();
  INSTRUMENTS.forEach(loadMarketCard);
  loadChart();
})();
