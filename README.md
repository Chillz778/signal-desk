# Personal Trading Intelligence Platform

A free, personal research and signal platform for BTC/USD, EUR/USD, XAU/USD, and GBP/USD. It has a Python API, a browser dashboard, four independent strategy rule sets, a cost-aware backtester, historical validation, paper forward testing, and optional Telegram alerts. It never connects to a broker or places orders.

The principles behind the system are deliberate: each strategy has its own exact confirmation rules and higher-timeframe context; no setup is an acceptable result; and historical performance is never treated as a promise. Forward results are tracked separately and compared with historical results before a strategy can be considered eligible for live alerts.

## What is built

- **Data engine:** Twelve Data OHLC candles are normalized to UTC. The system drops incomplete candles and blocks analysis if data is stale, malformed, or has unexplained gaps. No synthetic or fallback prices are used.
- **Four strategy families:** Trend following, liquidity sweep plus market structure shift (MSS), breakout/retest, and range-only mean reversion. Their confirmation rules are strategy-specific rather than a universal indicator checklist.
- **Backtesting and validation:** Causal, in-memory simulation uses completed candles, explicit spreads/slippage/commission, next-candle entries, stop-first handling when an OHLC candle touches stop and target, 70/30 chronological splits, and walk-forward windows.
- **Paper forward testing:** The scanner advances eligible candidate signals from new closed hourly candles and tracks entry, expiry, partial targets, stop, and net R outcomes with costs.
- **Drift monitoring:** Historical pass only permits paper forward testing. Forward outcomes are compared with out-of-sample historical expectancy and profit factor. A deteriorating strategy is suspended from new alerts.
- **Dashboard:** The API serves the dashboard from the same origin. It shows closed-candle charts, provider and worker health, signal lifecycle, strategy readiness, and a historical-validation form.
- **Telegram:** Optional alert delivery is gated behind historical and forward approval. Alerts are analysis only; there is no order execution.

## Storage and privacy

Historical candles are fetched for each calculation and held in memory. Backtest trades and equity curves are never written to local storage or Supabase. A validation run may save only compact strategy metrics/configuration so the scanner can remember its research gate. Supabase also holds short-lived signal lifecycle records and one worker-health row. Signal records are removed after the configured retention period (30 days by default). No brokerage trades or credentials are stored.

The included Supabase migration enables row-level security and grants the app's server-side role only. Keep `SUPABASE_SECRET_KEY` on the API/worker side; never put it in browser JavaScript, a public frontend setting, or a committed file.

## Run locally

Requires Python 3.11 or newer. From the project folder in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Set `TWELVE_DATA_API_KEY` in `.env`. To persist research status and scanner events, also set `SUPABASE_URL` and `SUPABASE_SECRET_KEY`, then apply `backend/migrations/001_minimal_application_state.sql` once in Supabase **SQL Editor**. Paste and run the migration as one script. The app does not automatically alter your hosted database.

Start the dashboard and API:

```powershell
uvicorn app.main:app --app-dir backend --reload --env-file .env
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The API health check is [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health); interactive endpoint details are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Run the bounded scanner once from the same project folder:

```powershell
python -m app.workers.scanner
```

The scanner reads `.env` itself. It requires a working Twelve Data key, Supabase project URL, server-side Supabase key, and the migration above. Telegram credentials are optional; set both bot token and chat ID or leave both blank.

Run all automated tests:

```powershell
python -m unittest discover -s backend/tests -v
```

## Use the dashboard

1. Start the API, then check that the market-data key is shown as configured.
2. Select a market and 1H/4H/1D chart. Dashboard chart requests use closed candles. Refreshing the four market tiles makes four small additional data requests.
3. Apply the Supabase migration to see worker and signal history persist across runs.
4. In **Validate a strategy**, enter your own account value, risk percentage, and broker cost estimates. Those fields are required because assumed zero costs or an invented account size would make the results misleading. A validation request uses historical data credits.
5. A historical pass only starts paper forward testing. It does not approve a live alert. The dashboard labels the stage and the monitor suspends strategies when mature forward results deteriorate.

A short history or few qualifying setups can produce **Need more data**. That is an intentional outcome, not a reason to lower safeguards blindly.

## Free market-data limits

The Twelve Data Basic plan currently lists 8 API credits per minute and 800 per day, with free real-time forex and crypto coverage. The scheduled scanner uses 12 candle requests per hourly run across four symbols and three timeframes: about 288 requests in a full day, before dashboard refreshes or manual validation runs. The code throttles scanner requests and does not download data when the scan is not running.

Twelve Data lists commodity access on paid tiers, while Basic access explicitly lists forex and crypto. XAU/USD may therefore be unavailable on your free account. The platform does not upgrade, charge, or substitute another source. If gold is denied, leave it disabled for analysis until a verified free source can be added.

The free plan and its coverage can change. Check the current [Twelve Data pricing and quotas](https://twelvedata.com/pricing) and [Basic-plan coverage](https://support.twelvedata.com/en/articles/5335783-trial) before relying on the usage estimate.

## Free hourly scheduling

The repository includes `.github/workflows/hourly-scan.yml`, which schedules a scan at minute 17 each hour and supports manual runs from the GitHub Actions page. GitHub requires a scheduled workflow to be on the repository's default branch; schedules can be delayed or occasionally dropped during high load. This is best-effort swing-timeframe monitoring, not a continuously running service or uptime guarantee.

After connecting this folder to the GitHub repository, add these under **Settings → Secrets and variables → Actions → New repository secret**:

- `TWELVE_DATA_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`
- `TELEGRAM_BOT_TOKEN` (optional)
- `TELEGRAM_CHAT_ID` (optional; needed with the bot token)

The workflow checks required secrets before installing/running the scanner and stores no workflow artifacts or market history. Keep the repository private if you do not want the source code public. GitHub Actions usage depends on repository visibility and your account's included quota; the workflow does not enable a paid plan or add a payment method. Review your Actions usage before leaving it scheduled.

GitHub documents that schedules run from the default branch and may be delayed during high-load periods: [scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions usage and billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

## API routes

- `GET /api/v1/market-data/{instrument}?timeframe=1h&limit=240` — validated candle history.
- `POST /api/v1/research/backtest` — one cost-aware backtest. Trade details and equity curve are returned only when `include_details` is explicitly true; they remain in memory.
- `POST /api/v1/research/validate` — in-sample, out-of-sample, and walk-forward validation; writes a compact summary only when Supabase is configured.
- `POST /api/v1/signals/evaluate` — evaluates a candidate without authorizing a live alert.
- `POST /api/v1/risk/position-size` — calculates a suggested position size from explicit user-provided account, risk, contract, and cost assumptions.
- `GET /api/v1/system/health`, `GET /api/v1/strategies/status`, and `GET /api/v1/signals/recent` — dashboard status endpoints.
- `GET /health` — lightweight API health check.

## Strategy notes

Each strategy can return no setup; strategies do not share a requirement that every indicator agrees.

- **Trend following:** Daily EMA direction and slope, completed 4H structure, and a bounded 1H breakout. Stop and 2R/3R targets are set by the strategy.
- **Liquidity sweep + MSS:** Repeated completed 4H levels, a sweep and close back through the level, then a confirming 1H structure shift.
- **Breakout retest:** A compressed completed 4H range, a strong close outside it, then a completed 1H retest.
- **Range mean reversion:** Daily/4H range regime, then a 1H Bollinger re-entry with RSI confirmation.

See the strategy modules under `backend/app/strategies/` for exact entry, invalidation, and expiry rules. All strategies use the same explicit data-quality, cost, and risk controls but their setup confirmations are separate.

## Important limits

This platform is research software, not investment advice. A backtest pass does not establish future profitability. Market-data vendors can revise history and forex/commodity feeds can differ from a broker. Without saved candles, a rerun may not reproduce an identical result. Live alerts are not brokerage orders and the application has no execution capability.
