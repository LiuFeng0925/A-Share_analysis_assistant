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
  PRIMARY KEY (market, code, period, adjustment, bar_time)
);

CREATE TABLE IF NOT EXISTS ingestion_run (
  run_id UUID PRIMARY KEY,
  kind VARCHAR NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  source VARCHAR NOT NULL,
  row_count BIGINT NOT NULL DEFAULT 0,
  status VARCHAR NOT NULL,
  error_message VARCHAR
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(str(path))
        self.lock = RLock()
        self.initialize()

    def initialize(self) -> None:
        with self.lock:
            self.connection.execute(SCHEMA_SQL)

    def close(self) -> None:
        with self.lock:
            self.connection.close()
