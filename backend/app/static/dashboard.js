(() => {
  "use strict";

  const INSTRUMENTS = ["BTCUSD", "EURUSD", "XAUUSD", "GBPUSD"];
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
      historical_pass_forward_test_required: "Historical pass · outcomes monitoring",
      approved_for_live: "Historical and forward checked",
      suspended: "Forward drift noted",
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
      const websiteTelegramReady = health.telegram_configured_on_website === true;
      setDot(
        "telegram-dot",
        websiteTelegramReady || telegram === "healthy" ? "ok" : "warn",
        "telegram-status",
        websiteTelegramReady ? "Ready" : telegram === "healthy" ? "Scheduled worker ready" : "Add to website host",
      );
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
    strategy.textContent = `${niceStatus(signal.strategy_name)} · Signal alert · historical status: ${niceStatus(data.validation_status || "testing")}`;
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
    alert.textContent = signal.mode === "live" ? `Telegram alert: ${niceStatus(signal.alert_status)}` : `Paper · ${niceStatus(signal.alert_status)}`;
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

  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

  async function waitForScan(scanId) {
    const started = Date.now();
    while (Date.now() - started < 180_000) {
      await sleep(3500);
      const current = await request(`/api/v1/scanner/run/${encodeURIComponent(scanId)}`);
      if (current.status !== "starting" && current.status !== "running") return current;
      el("scan-feedback").textContent = "Scanning all markets and strategies. This can take about two minutes on the free data plan…";
    }
    throw new Error("The scan is taking longer than expected. Refresh the dashboard to check its status.");
  }

  async function runManualScan() {
    const button = el("run-scan");
    const feedback = el("scan-feedback");
    button.disabled = true;
    button.textContent = "Starting scan…";
    feedback.textContent = "Starting a scan of every market and strategy…";
    try {
      const started = await request("/api/v1/scanner/run", { method: "POST", body: "{}" });
      const completed = await waitForScan(started.scan_id);
      if (completed.status === "already_running") {
        feedback.textContent = "A scheduled scan is already running. The hourly schedule is still active.";
      } else if (completed.status === "failed") {
        feedback.textContent = "The scan could not finish. Check the system status above and try again later.";
      } else {
        const result = completed.result || {};
        const created = Number(result.signals_created || 0);
        const sent = Number(result.alerts_sent || 0);
        const parts = [`Scan finished across ${result.scanned_instruments || 0} markets`, `${created} new signal${created === 1 ? "" : "s"}`, `${sent} Telegram alert${sent === 1 ? "" : "s"} sent`];
        if (result.telegram_status === "not_configured") parts.push("Telegram is not configured on this website host");
        if (result.alerts_failed) parts.push(`${result.alerts_failed} Telegram delivery failure${result.alerts_failed === 1 ? "" : "s"}`);
        if (result.worker_status === "degraded") parts.push("some market data was unavailable");
        feedback.textContent = created === 0 ? `Scan finished. No setup met its rules; no signal was forced. ${parts.slice(0, 1).join("")}.` : parts.join(" · ");
      }
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      button.disabled = false;
      button.textContent = "Run scan now";
      await Promise.all([loadSystem(), loadSignals()]);
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
    loadSignals();
    INSTRUMENTS.forEach(loadMarketCard);
    loadChart();
  });
  el("run-scan").addEventListener("click", runManualScan);
  window.addEventListener("resize", () => renderChart(state.candles));

  loadSystem();
  loadSignals();
  INSTRUMENTS.forEach(loadMarketCard);
  loadChart();
})();
