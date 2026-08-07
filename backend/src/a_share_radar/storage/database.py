from pathlib import Path
from threading import RLock

import duckdb

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stock_master (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  name VARCHAR NOT NULL,
  list_status VARCHAR NOT NULL,
  list_date DATE,
  updated_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (market, code)
);

CREATE TABLE IF NOT EXISTS latest_quote (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL,
  latest_price DOUBLE,
  change_percent DOUBLE,
  change_amount DOUBLE,
  open_price DOUBLE,
  high_price DOUBLE,
  low_price DOUBLE,
  previous_close DOUBLE,
  volume BIGINT,
  amount DOUBLE,
  turnover_rate DOUBLE,
  total_market_cap DOUBLE,
  source VARCHAR NOT NULL,
  quality_status VARCHAR NOT NULL,
  PRIMARY KEY (market, code)
);

CREATE TABLE IF NOT EXISTS quote_snapshot_hot AS
SELECT * FROM latest_quote WHERE FALSE;

CREATE UNIQUE INDEX IF NOT EXISTS snapshot_identity
ON quote_snapshot_hot (market, code, captured_at);

CREATE TABLE IF NOT EXISTS bar_hot (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  period VARCHAR NOT NULL,
  adjustment VARCHAR NOT NULL,
  bar_time TIMESTAMPTZ NOT NULL,
  open_price DOUBLE NOT NULL,
  high_price DOUBLE NOT NULL,
  low_price DOUBLE NOT NULL,
  close_price DOUBLE NOT NULL,
  volume BIGINT NOT NULL,
  amount DOUBLE NOT NULL,
  source VARCHAR NOT NULL,
  is_complete BOOLEAN NOT NULL,
  acquired_at TIMESTAMPTZ NOT NULL,
  quality_status VARCHAR NOT NULL,
  PRIMARY KEY (market, code, period, adjustment, bar_time)
);

CREATE TABLE IF NOT EXISTS ingestion_run (
  run_id UUID PRIMARY KEY,
  kind VARCHAR NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  source VARCHAR NOT NULL,
  market_time TIMESTAMPTZ,
  expected_row_count BIGINT,
  actual_row_count BIGINT,
  row_count BIGINT NOT NULL DEFAULT 0,
  status VARCHAR NOT NULL,
  quality_status VARCHAR,
  error_message VARCHAR,
  market VARCHAR,
  code VARCHAR,
  period VARCHAR,
  adjustment VARCHAR,
  invalid_row_count BIGINT
);

CREATE TABLE IF NOT EXISTS bar_range_check (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  period VARCHAR NOT NULL,
  adjustment VARCHAR NOT NULL,
  range_start TIMESTAMPTZ NOT NULL,
  range_end TIMESTAMPTZ NOT NULL,
  checked_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  source VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  quality_status VARCHAR NOT NULL,
  raw_row_count BIGINT NOT NULL,
  valid_row_count BIGINT NOT NULL,
  invalid_row_count BIGINT NOT NULL,
  PRIMARY KEY (
    market, code, period, adjustment, range_start, range_end
  )
);

CREATE TABLE IF NOT EXISTS market_summary_batch (
  run_id UUID PRIMARY KEY,
  market_time TIMESTAMPTZ NOT NULL,
  total BIGINT NOT NULL,
  rising BIGINT NOT NULL,
  falling BIGINT NOT NULL,
  flat BIGINT NOT NULL,
  amount DOUBLE NOT NULL,
  source VARCHAR NOT NULL,
  quality_status VARCHAR NOT NULL,
  finished_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS trading_calendar (
  trade_date DATE PRIMARY KEY,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS indicator_macd_latest (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  period VARCHAR NOT NULL,
  calculated_at TIMESTAMPTZ NOT NULL,
  market_time TIMESTAMPTZ,
  diff DOUBLE,
  dea DOUBLE,
  histogram DOUBLE,
  signal_type VARCHAR NOT NULL,
  signal_date DATE,
  recent_signal_days INTEGER,
  recent_signal_label VARCHAR NOT NULL,
  zero_axis VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  is_intraday BOOLEAN NOT NULL,
  quality VARCHAR NOT NULL,
  PRIMARY KEY (market, code, period)
);

CREATE TABLE IF NOT EXISTS indicator_macd_series (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  period VARCHAR NOT NULL,
  bar_time TIMESTAMPTZ NOT NULL,
  diff DOUBLE,
  dea DOUBLE,
  histogram DOUBLE,
  signal_type VARCHAR NOT NULL,
  zero_axis VARCHAR NOT NULL,
  is_intraday BOOLEAN NOT NULL,
  quality VARCHAR NOT NULL,
  PRIMARY KEY (market, code, period, bar_time)
);

CREATE TABLE IF NOT EXISTS indicator_macd_divergence (
  market VARCHAR NOT NULL,
  code VARCHAR NOT NULL,
  period VARCHAR NOT NULL,
  direction VARCHAR NOT NULL,
  status VARCHAR NOT NULL,
  anchor_one_time TIMESTAMPTZ NOT NULL,
  anchor_one_price DOUBLE NOT NULL,
  anchor_one_diff DOUBLE NOT NULL,
  anchor_two_time TIMESTAMPTZ NOT NULL,
  anchor_two_price DOUBLE NOT NULL,
  anchor_two_diff DOUBLE NOT NULL,
  pivot_time TIMESTAMPTZ NOT NULL,
  pivot_price DOUBLE NOT NULL,
  pivot_diff DOUBLE NOT NULL,
  detected_at TIMESTAMPTZ NOT NULL,
  confirmed_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  is_valid BOOLEAN NOT NULL,
  corresponding_signal VARCHAR NOT NULL,
  corresponding_signal_time TIMESTAMPTZ,
  recent_days INTEGER NOT NULL,
  PRIMARY KEY (market, code, period, direction, detected_at)
);

CREATE INDEX IF NOT EXISTS indicator_macd_divergence_filter
ON indicator_macd_divergence
  (market, code, period, direction, status, is_valid, recent_days);
"""

MIGRATION_SQL = (
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS market_time TIMESTAMPTZ",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS expected_row_count BIGINT",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS actual_row_count BIGINT",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS quality_status VARCHAR",
    "ALTER TABLE bar_hot ADD COLUMN IF NOT EXISTS acquired_at TIMESTAMPTZ",
    "ALTER TABLE bar_hot ADD COLUMN IF NOT EXISTS quality_status VARCHAR",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS market VARCHAR",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS code VARCHAR",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS period VARCHAR",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS adjustment VARCHAR",
    "ALTER TABLE ingestion_run ADD COLUMN IF NOT EXISTS invalid_row_count BIGINT",
    "ALTER TABLE bar_range_check ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    ("UPDATE ingestion_run SET actual_row_count = row_count WHERE actual_row_count IS NULL"),
    "UPDATE bar_range_check SET expires_at = checked_at WHERE expires_at IS NULL",
    "UPDATE bar_hot SET acquired_at = bar_time WHERE acquired_at IS NULL",
    (
        "UPDATE bar_hot SET quality_status = "
        "CASE WHEN is_complete THEN 'ok' ELSE 'partial' END "
        "WHERE quality_status IS NULL"
    ),
)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.lock = RLock()
        self.initialize()

    def initialize(self) -> None:
        with self.lock:
            self.connection.execute(SCHEMA_SQL)
            for statement in MIGRATION_SQL:
                self.connection.execute(statement)

    def close(self) -> None:
        with self.lock:
            self.connection.close()
