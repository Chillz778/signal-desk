-- Compact application state only. Historical candles, backtest trades, and
-- backtest equity curves are never persisted by this schema.

create table if not exists public.strategy_status (
    strategy_name text not null,
    strategy_version text not null,
    instrument text not null check (instrument in ('BTCUSD', 'EURUSD', 'XAUUSD', 'GBPUSD')),
    status text not null check (
        status in (
            'testing',
            'historical_pass_forward_test_required',
            'approved_for_live',
            'suspended',
            'historical_rejected',
            'insufficient_data'
        )
    ),
    historical_summary jsonb not null default '{}'::jsonb check (jsonb_typeof(historical_summary) = 'object'),
    forward_summary jsonb not null default '{}'::jsonb check (jsonb_typeof(forward_summary) = 'object'),
    config jsonb not null default '{}'::jsonb check (jsonb_typeof(config) = 'object'),
    updated_at timestamptz not null default now(),
    primary key (strategy_name, strategy_version, instrument)
);

create table if not exists public.signal_events (
    signal_id text primary key,
    instrument text not null check (instrument in ('BTCUSD', 'EURUSD', 'XAUUSD', 'GBPUSD')),
    strategy_name text not null,
    strategy_version text not null,
    mode text not null check (mode in ('forward_test', 'live')),
    side text not null check (side in ('long', 'short')),
    detected_at timestamptz not null,
    expires_at timestamptz not null,
    entry_reference double precision not null check (entry_reference > 0),
    entry_zone_low double precision not null check (entry_zone_low > 0),
    entry_zone_high double precision not null check (entry_zone_high >= entry_zone_low),
    stop_loss double precision not null check (stop_loss > 0),
    take_profits jsonb not null check (jsonb_typeof(take_profits) = 'array'),
    target_weights jsonb not null check (jsonb_typeof(target_weights) = 'array'),
    signal_data jsonb not null check (jsonb_typeof(signal_data) = 'object'),
    status text not null check (status in ('developing', 'confirmed', 'expired', 'invalidated', 'completed')),
    entry_filled_at timestamptz,
    entry_filled_price double precision,
    initial_risk_per_unit double precision,
    remaining_fraction double precision not null default 1 check (remaining_fraction between 0 and 1),
    realized_r_multiple double precision not null default 0,
    next_target_index smallint not null default 0 check (next_target_index >= 0),
    last_processed_candle_at timestamptz,
    outcome text check (outcome in ('stop', 'target_1', 'target_2', 'expired', 'invalidated')),
    outcome_r_multiple double precision,
    outcome_at timestamptz,
    alert_status text not null default 'not_sent' check (alert_status in ('not_sent', 'sending', 'sent', 'failed', 'suppressed')),
    alert_attempts smallint not null default 0 check (alert_attempts >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists signal_events_active_expiry_idx
    on public.signal_events (expires_at)
    where status in ('developing', 'confirmed');

create index if not exists signal_events_strategy_outcome_idx
    on public.signal_events (strategy_name, strategy_version, instrument, outcome_at desc)
    where outcome_r_multiple is not null;

create index if not exists signal_events_retention_idx
    on public.signal_events (created_at);

create table if not exists public.worker_health (
    worker_name text primary key,
    status text not null check (status in ('starting', 'healthy', 'degraded', 'failed')),
    last_scan_at timestamptz,
    next_scan_at timestamptz,
    provider_status jsonb not null default '{}'::jsonb check (jsonb_typeof(provider_status) = 'object'),
    telegram_status text not null default 'not_configured',
    last_error_code text,
    updated_at timestamptz not null default now()
);

alter table public.strategy_status enable row level security;
alter table public.signal_events enable row level security;
alter table public.worker_health enable row level security;

revoke all on public.strategy_status, public.signal_events, public.worker_health
    from anon, authenticated;
grant usage on schema public to service_role;
grant select, insert, update, delete on public.strategy_status to service_role;
grant select, insert, update, delete on public.signal_events to service_role;
grant select, insert, update, delete on public.worker_health to service_role;
