from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from a_share_radar.domain.indicators import (
    KdjCalculation,
    KdjPoint,
    KdjQuality,
    KdjSignal,
    KdjSignalZone,
    KdjSummary,
    KdjZone,
    MacdCalculation,
    MacdPoint,
    MacdQuality,
    MacdSignal,
    MacdSummary,
    ZeroAxisPosition,
)
from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock
from a_share_radar.storage.database import Database


@dataclass(frozen=True, slots=True)
class StockQuoteRow:
    code: str
    market: Market
    name: str
    list_status: str
    list_date: date | None
    captured_at: datetime | None
    latest_price: float | None
    change_percent: float | None
    change_amount: float | None
    open_price: float | None
    high_price: float | None
    low_price: float | None
    previous_close: float | None
    volume: int | None
    amount: float | None
    turnover_rate: float | None
    total_market_cap: float | None
    source: str | None
    quality_status: QualityStatus | None
    macd_signal_type: MacdSignal | None = None
    macd_signal_date: date | None = None
    macd_recent_signal_days: int | None = None
    macd_signal_label: str | None = None
    macd_zero_axis: ZeroAxisPosition | None = None
    macd_quality: MacdQuality | None = None
    kdj_signal_type: KdjSignal | None = None
    kdj_signal_time: datetime | None = None
    kdj_recent_signal_days: int | None = None
    kdj_signal_label: str | None = None
    kdj_signal_zone: KdjSignalZone | None = None
    kdj_current_zone: KdjZone | None = None
    kdj_quality: KdjQuality | None = None


@dataclass(frozen=True, slots=True)
class StockPage:
    items: list[StockQuoteRow]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class MarketSummary:
    total: int
    rising: int
    falling: int
    flat: int
    amount: float
    market_status: Literal["open", "closed"]
    last_updated_at: datetime | None
    stale: bool


@dataclass(frozen=True, slots=True)
class DataStatus:
    stock_count: int
    latest_quote_count: int
    snapshot_count: int
    bar_count: int
    latest_captured_at: datetime | None
    latest_success_at: datetime | None
    latest_failure_at: datetime | None
    latest_market_time: datetime | None
    snapshot_expected_count: int | None
    snapshot_actual_count: int | None
    snapshot_coverage_ratio: float | None
    snapshot_quality_status: str | None


@dataclass(frozen=True, slots=True)
class SnapshotCommitResult:
    run_id: UUID
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class BarIngestionAudit:
    acquired_at: datetime
    source: str
    quality_status: str
    raw_row_count: int
    valid_row_count: int
    invalid_row_count: int
    status: str


SORT_COLUMNS = {
    "code": "s.code",
    "latest_price": "q.latest_price",
    "change_percent": "q.change_percent",
    "amount": "q.amount",
    "turnover_rate": "q.turnover_rate",
    "total_market_cap": "q.total_market_cap",
}

_QUOTE_COLUMNS = """
    s.code, s.market, s.name, s.list_status, s.list_date,
    CAST(q.captured_at AS VARCHAR), q.latest_price, q.change_percent, q.change_amount,
    q.open_price, q.high_price, q.low_price, q.previous_close,
    q.volume, q.amount, q.turnover_rate, q.total_market_cap,
    q.source, q.quality_status,
    m.signal_type, CAST(m.signal_date AS VARCHAR), m.recent_signal_days,
    m.recent_signal_label, m.zero_axis, m.quality,
    kdj.signal_type, CAST(kdj.signal_time AS VARCHAR), kdj.recent_signal_days,
    kdj.recent_signal_label, kdj.signal_zone, kdj.current_zone, kdj.quality
"""
_MACD_JOIN = """
    LEFT JOIN indicator_macd_latest m
      ON m.market = s.market AND m.code = s.code AND m.period = '1d'
"""
_KDJ_JOIN = """
    LEFT JOIN indicator_kdj_latest kdj
      ON kdj.market = s.market AND kdj.code = s.code AND kdj.period = '1d'
"""
_MACD_RECENT_WINDOWS = {"today": 0, "3d": 2, "5d": 4}
_KDJ_RECENT_WINDOWS = {"today": 0, "3d": 2, "5d": 4}

_SNAPSHOT_BATCH_RELATION = "snapshot_batch_input"
_ARCHIVE_EXISTING_RELATION = "archive_existing_input"
_ARCHIVE_HOT_RELATION = "archive_hot_input"
_SNAPSHOT_SCHEMA = pa.schema(
    [
        ("market", pa.string()),
        ("code", pa.string()),
        ("name", pa.string()),
        ("captured_at", pa.timestamp("us", tz="UTC")),
        ("latest_price", pa.float64()),
        ("change_percent", pa.float64()),
        ("change_amount", pa.float64()),
        ("open_price", pa.float64()),
        ("high_price", pa.float64()),
        ("low_price", pa.float64()),
        ("previous_close", pa.float64()),
        ("volume", pa.int64()),
        ("amount", pa.float64()),
        ("turnover_rate", pa.float64()),
        ("total_market_cap", pa.float64()),
        ("source", pa.string()),
        ("quality_status", pa.string()),
    ]
)


class MarketRepository:
    def __init__(self, database: Database):
        self.database = database

    def upsert_stocks(self, stocks: Iterable[Stock]) -> None:
        rows = [
            (
                stock.market.value,
                stock.code,
                stock.name,
                stock.list_status,
                stock.list_date,
                datetime.now(UTC),
            )
            for stock in stocks
        ]
        if not rows:
            return
        with self.database.lock:
            self._transactional_executemany(
                """
                INSERT INTO stock_master
                (market, code, name, list_status, list_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (market, code) DO UPDATE SET
                  name = EXCLUDED.name,
                  list_status = EXCLUDED.list_status,
                  list_date = COALESCE(EXCLUDED.list_date, stock_master.list_date),
                  updated_at = EXCLUDED.updated_at
                """,
                rows,
            )

    def save_snapshot(self, quotes: Iterable[QuoteSnapshot]) -> None:
        rows = [self._quote_values(quote) for quote in quotes]
        if not rows:
            return
        batch = self._snapshot_batch(rows)
        with self.database.lock:
            connection = self.database.connection
            registered = False
            transaction_started = False
            try:
                connection.register(_SNAPSHOT_BATCH_RELATION, batch)
                registered = True
                connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._insert_snapshot_history(connection)
                self._advance_latest_quotes(connection)
                connection.execute("COMMIT")
                transaction_started = False
            except Exception:
                if transaction_started:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if registered:
                    connection.unregister(_SNAPSHOT_BATCH_RELATION)

    def commit_snapshot_success(
        self,
        quotes: Iterable[QuoteSnapshot],
        *,
        started_at: datetime,
        source: str,
        market_time: datetime,
        expected_row_count: int,
        quality_status: str,
    ) -> SnapshotCommitResult:
        rows = [self._quote_values(quote) for quote in quotes]
        if not rows:
            raise ValueError("成功快照批次不能为空")
        batch = self._snapshot_batch(rows)
        run_id = uuid4()
        with self.database.lock:
            connection = self.database.connection
            registered = False
            transaction_started = False
            try:
                connection.register(_SNAPSHOT_BATCH_RELATION, batch)
                registered = True
                connection.execute("BEGIN TRANSACTION")
                transaction_started = True
                self._insert_snapshot_history(connection)
                self._advance_latest_quotes(connection)
                stock_updated_at = datetime.now(UTC)
                connection.execute(
                    f"""
                    UPDATE stock_master AS stock
                    SET name = batch.name, updated_at = ?
                    FROM (
                        SELECT *, ROW_NUMBER() OVER (
                            PARTITION BY market, code
                            ORDER BY captured_at DESC
                        ) AS batch_row
                        FROM {_SNAPSHOT_BATCH_RELATION}
                    ) AS batch
                    WHERE batch.batch_row = 1
                      AND stock.market = batch.market
                      AND stock.code = batch.code
                      AND stock.name <> batch.name
                    """,
                    [stock_updated_at],
                )
                finished_at = datetime.now(UTC)
                connection.execute(
                    """
                    INSERT INTO ingestion_run (
                      run_id, kind, started_at, finished_at, source, market_time,
                      expected_row_count, actual_row_count, row_count, status,
                      quality_status, error_message
                    ) VALUES (?, 'snapshot', ?, ?, ?, ?, ?, ?, ?, 'success', ?, NULL)
                    """,
                    (
                        str(run_id),
                        started_at,
                        finished_at,
                        source,
                        market_time,
                        expected_row_count,
                        len(rows),
                        len(rows),
                        quality_status,
                    ),
                )
                connection.execute(
                    f"""
                    INSERT INTO market_summary_batch
                    SELECT
                      ?, ?, COUNT(*),
                      COUNT(*) FILTER (WHERE change_percent > 0),
                      COUNT(*) FILTER (WHERE change_percent < 0),
                      COUNT(*) FILTER (WHERE change_percent = 0),
                      COALESCE(SUM(amount), 0.0), ?, ?, ?
                    FROM {_SNAPSHOT_BATCH_RELATION}
                    """,
                    (
                        str(run_id),
                        market_time,
                        source,
                        quality_status,
                        finished_at,
                    ),
                )
                connection.execute("COMMIT")
                transaction_started = False
            except Exception:
                if transaction_started:
                    connection.execute("ROLLBACK")
                raise
            finally:
                if registered:
                    connection.unregister(_SNAPSHOT_BATCH_RELATION)
        return SnapshotCommitResult(run_id=run_id, finished_at=finished_at)

    @staticmethod
    def _insert_snapshot_history(connection) -> None:
        connection.execute(
            f"""
            INSERT OR REPLACE INTO quote_snapshot_hot
            SELECT
              market, code, captured_at, latest_price, change_percent,
              change_amount, open_price, high_price, low_price,
              previous_close, volume, amount, turnover_rate,
              total_market_cap, source, quality_status
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY market, code, captured_at
                    ORDER BY captured_at DESC
                ) AS batch_row
                FROM {_SNAPSHOT_BATCH_RELATION}
            )
            WHERE batch_row = 1
            """
        )

    @staticmethod
    def _advance_latest_quotes(connection) -> None:
        connection.execute(
            f"""
            INSERT OR REPLACE INTO latest_quote
            SELECT
              batch.market, batch.code, batch.captured_at,
              batch.latest_price, batch.change_percent, batch.change_amount,
              batch.open_price, batch.high_price, batch.low_price,
              batch.previous_close, batch.volume, batch.amount,
              batch.turnover_rate, batch.total_market_cap,
              batch.source, batch.quality_status
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY market, code
                    ORDER BY captured_at DESC
                ) AS batch_row
                FROM {_SNAPSHOT_BATCH_RELATION}
            ) AS batch
            LEFT JOIN latest_quote AS current
              ON current.market = batch.market AND current.code = batch.code
            WHERE batch.batch_row = 1
              AND batch.latest_price IS NOT NULL
              AND batch.volume IS NOT NULL
              AND batch.amount IS NOT NULL
              AND batch.latest_price >= 0
              AND (batch.open_price IS NULL OR batch.open_price >= 0)
              AND (batch.high_price IS NULL OR batch.high_price >= 0)
              AND (batch.low_price IS NULL OR batch.low_price >= 0)
              AND (batch.previous_close IS NULL OR batch.previous_close >= 0)
              AND batch.volume >= 0
              AND batch.amount >= 0
              AND (
                batch.high_price IS NULL
                OR batch.low_price IS NULL
                OR batch.high_price >= batch.low_price
              )
              AND (
                batch.open_price IS NULL
                OR batch.high_price IS NULL
                OR batch.low_price IS NULL
                OR batch.open_price BETWEEN batch.low_price AND batch.high_price
              )
              AND (
                batch.high_price IS NULL
                OR batch.low_price IS NULL
                OR batch.latest_price BETWEEN batch.low_price AND batch.high_price
              )
              AND (
                current.captured_at IS NULL
                OR batch.captured_at >= current.captured_at
              )
            """
        )

    def snapshot_expectation(self, minimum_expected_count: int) -> int:
        with self.database.lock:
            row = self.database.connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM stock_master),
                  COALESCE((
                    SELECT actual_row_count
                    FROM ingestion_run
                    WHERE kind = 'snapshot' AND status = 'success'
                    ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                    LIMIT 1
                  ), 0)
                """
            ).fetchone()
        return max(minimum_expected_count, int(row[0]), int(row[1]))

    def stock_identities(self) -> set[tuple[Market, str]]:
        with self.database.lock:
            rows = self.database.connection.execute(
                "SELECT market, code FROM stock_master"
            ).fetchall()
        return {(Market(row[0]), row[1]) for row in rows}

    def refresh_stock_names(self, quotes: Iterable[QuoteSnapshot]) -> None:
        rows = [(quote.name, datetime.now(UTC), quote.market.value, quote.code) for quote in quotes]
        if not rows:
            return
        with self.database.lock:
            self._transactional_executemany(
                """
                UPDATE stock_master SET name = ?, updated_at = ?
                WHERE market = ? AND code = ? AND name <> ?
                """,
                [(*row, row[0]) for row in rows],
            )

    def record_ingestion_run(
        self,
        *,
        kind: str,
        started_at: datetime,
        finished_at: datetime,
        source: str,
        market_time: datetime | None,
        expected_row_count: int,
        actual_row_count: int,
        status: str,
        quality_status: str,
        error_message: str | None = None,
    ) -> None:
        with self.database.lock:
            self.database.connection.execute(
                """
                INSERT INTO ingestion_run (
                  run_id, kind, started_at, finished_at, source, market_time,
                  expected_row_count, actual_row_count, row_count, status,
                  quality_status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    kind,
                    started_at,
                    finished_at,
                    source,
                    market_time,
                    expected_row_count,
                    actual_row_count,
                    actual_row_count,
                    status,
                    quality_status,
                    error_message,
                ),
            )

    def record_bar_ingestion(
        self,
        *,
        market: Market,
        code: str,
        period: str,
        adjustment: str,
        started_at: datetime,
        acquired_at: datetime,
        source: str,
        market_time: datetime | None,
        raw_row_count: int,
        valid_row_count: int,
        invalid_row_count: int,
        status: str,
        quality_status: str,
        error_message: str | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        expires_at: datetime | None = None,
        confirmed_ranges: Iterable[tuple[datetime, datetime]] | None = None,
    ) -> None:
        with self.database.lock:
            connection = self.database.connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO ingestion_run (
                      run_id, kind, started_at, finished_at, source, market_time,
                      expected_row_count, actual_row_count, row_count, status,
                      quality_status, error_message, market, code, period,
                      adjustment, invalid_row_count
                    ) VALUES (?, 'bar', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        started_at,
                        acquired_at,
                        source,
                        market_time,
                        raw_row_count,
                        valid_row_count,
                        valid_row_count,
                        status,
                        quality_status,
                        error_message,
                        market.value,
                        code,
                        period,
                        adjustment,
                        invalid_row_count,
                    ),
                )
                if confirmed_ranges is None:
                    range_rows = (
                        []
                        if range_start is None or range_end is None
                        else [
                            (
                                range_start,
                                range_end,
                                self._bar_range_status(
                                    status, quality_status, valid_row_count
                                ),
                                raw_row_count,
                                valid_row_count,
                                invalid_row_count,
                            )
                        ]
                    )
                else:
                    range_rows = [
                        (confirmed_start, confirmed_end, "success", 1, 1, 0)
                        for confirmed_start, confirmed_end in confirmed_ranges
                    ]
                if range_rows:
                    connection.executemany(
                        """
                        INSERT INTO bar_range_check (
                          market, code, period, adjustment, range_start,
                          range_end, checked_at, expires_at, source, status,
                          quality_status, raw_row_count, valid_row_count,
                          invalid_row_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (
                          market, code, period, adjustment, range_start, range_end
                        ) DO UPDATE SET
                          checked_at = EXCLUDED.checked_at,
                          expires_at = EXCLUDED.expires_at,
                          source = EXCLUDED.source,
                          status = EXCLUDED.status,
                          quality_status = EXCLUDED.quality_status,
                          raw_row_count = EXCLUDED.raw_row_count,
                          valid_row_count = EXCLUDED.valid_row_count,
                          invalid_row_count = EXCLUDED.invalid_row_count
                        """,
                        [
                            (
                                market.value,
                                code,
                                period,
                                adjustment,
                                confirmed_start,
                                confirmed_end,
                                acquired_at,
                                expires_at or acquired_at,
                                source,
                                range_status,
                                quality_status,
                                confirmed_raw_count,
                                confirmed_valid_count,
                                confirmed_invalid_count,
                            )
                            for (
                                confirmed_start,
                                confirmed_end,
                                range_status,
                                confirmed_raw_count,
                                confirmed_valid_count,
                                confirmed_invalid_count,
                            ) in range_rows
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _bar_range_status(
        ingestion_status: str, quality_status: str, valid_row_count: int
    ) -> str:
        if ingestion_status == "failed" or quality_status == "error":
            return "failed"
        if quality_status != "ok":
            return "partial"
        return "success_empty" if valid_row_count == 0 else "success"

    def list_confirmed_bar_ranges(
        self,
        market: Market,
        code: str,
        period: str,
        adjustment: str,
        range_start: datetime,
        range_end: datetime,
        at: datetime,
    ) -> list[tuple[datetime, datetime]]:
        with self.database.lock:
            rows = self.database.connection.execute(
                """
                SELECT CAST(range_start AS VARCHAR), CAST(range_end AS VARCHAR)
                FROM bar_range_check
                WHERE market = ? AND code = ? AND period = ? AND adjustment = ?
                  AND status IN ('success', 'success_empty')
                  AND quality_status = 'ok' AND expires_at > ?
                  AND range_end >= ? AND range_start <= ?
                ORDER BY range_start, range_end
                """,
                (
                    market.value,
                    code,
                    period,
                    adjustment,
                    at,
                    range_start,
                    range_end,
                ),
            ).fetchall()
        return [
            (datetime.fromisoformat(row[0]), datetime.fromisoformat(row[1]))
            for row in rows
        ]

    def latest_bar_ingestion(
        self, market: Market, code: str, period: str, adjustment: str
    ) -> BarIngestionAudit | None:
        with self.database.lock:
            row = self.database.connection.execute(
                """
                SELECT CAST(finished_at AS VARCHAR), source, quality_status,
                       expected_row_count, actual_row_count,
                       COALESCE(invalid_row_count, 0), status
                FROM ingestion_run
                WHERE kind = 'bar' AND market = ? AND code = ?
                  AND period = ? AND adjustment = ?
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                LIMIT 1
                """,
                (market.value, code, period, adjustment),
            ).fetchone()
        if row is None:
            return None
        return BarIngestionAudit(
            acquired_at=datetime.fromisoformat(row[0]),
            source=row[1],
            quality_status=row[2],
            raw_row_count=row[3],
            valid_row_count=row[4],
            invalid_row_count=row[5],
            status=row[6],
        )

    def replace_trading_days(self, trading_days: set[date]) -> None:
        rows = [(trade_date, datetime.now(UTC)) for trade_date in sorted(trading_days)]
        with self.database.lock:
            connection = self.database.connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute("DELETE FROM trading_calendar")
                if rows:
                    connection.executemany(
                        "INSERT INTO trading_calendar VALUES (?, ?)", rows
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_trading_days(self) -> set[date]:
        with self.database.lock:
            rows = self.database.connection.execute(
                "SELECT trade_date FROM trading_calendar"
            ).fetchall()
        return {row[0] for row in rows}

    def upsert_bars(self, bars: Iterable[Bar]) -> None:
        rows = [
            (
                bar.market.value,
                bar.code,
                bar.period,
                bar.adjustment,
                bar.bar_time,
                bar.open_price,
                bar.high_price,
                bar.low_price,
                bar.close_price,
                bar.volume,
                bar.amount,
                bar.source,
                bar.is_complete,
                bar.acquired_at,
                bar.quality_status.value,
            )
            for bar in bars
        ]
        if not rows:
            return
        with self.database.lock:
            self._transactional_executemany(
                """
                INSERT OR REPLACE INTO bar_hot (
                  market, code, period, adjustment, bar_time,
                  open_price, high_price, low_price, close_price,
                  volume, amount, source, is_complete, acquired_at, quality_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def list_all_stocks(self) -> list[Stock]:
        with self.database.lock:
            rows = self.database.connection.execute(
                """
                SELECT code, market, name, list_status, list_date
                FROM stock_master ORDER BY market, code
                """
            ).fetchall()
        return [
            Stock(
                code=row[0],
                market=Market(row[1]),
                name=row[2],
                list_status=row[3],
                list_date=row[4],
            )
            for row in rows
        ]

    def list_stocks(
        self,
        query: str | None,
        market: Market | None,
        sort_by: str,
        sort_order: str,
        page: int,
        page_size: int,
        macd_signal: str | None = None,
        macd_zero_axis: str | None = None,
        macd_recent_window: str | None = None,
        kdj_signal: str | None = None,
        kdj_signal_zone: str | None = None,
        kdj_recent_window: str | None = None,
    ) -> StockPage:
        if sort_by not in SORT_COLUMNS:
            raise ValueError(f"不支持的排序字段：{sort_by}")
        normalized_order = sort_order.lower()
        if normalized_order not in {"asc", "desc"}:
            raise ValueError(f"不支持的排序方向：{sort_order}")
        if page < 1 or page_size < 1:
            raise ValueError("页码和每页数量必须大于零")
        if macd_signal is not None and macd_signal not in {
            MacdSignal.GOLDEN_CROSS.value,
            MacdSignal.DEATH_CROSS.value,
        }:
            raise ValueError(f"不支持的 MACD 信号：{macd_signal}")
        if macd_zero_axis is not None and macd_zero_axis not in {
            ZeroAxisPosition.ABOVE.value,
            ZeroAxisPosition.BELOW.value,
        }:
            raise ValueError(f"不支持的 MACD 零轴位置：{macd_zero_axis}")
        if macd_recent_window is not None and macd_recent_window not in _MACD_RECENT_WINDOWS:
            raise ValueError(f"不支持的 MACD 最近时间：{macd_recent_window}")
        if kdj_signal is not None and kdj_signal not in {
            KdjSignal.GOLDEN_CROSS.value,
            KdjSignal.DEATH_CROSS.value,
        }:
            raise ValueError(f"不支持的 KDJ 信号：{kdj_signal}")
        if kdj_signal_zone is not None and kdj_signal_zone not in {
            KdjSignalZone.LOW.value,
            KdjSignalZone.MIDDLE.value,
            KdjSignalZone.HIGH.value,
        }:
            raise ValueError(f"不支持的 KDJ 信号区域：{kdj_signal_zone}")
        if kdj_recent_window is not None and kdj_recent_window not in _KDJ_RECENT_WINDOWS:
            raise ValueError(f"不支持的 KDJ 最近时间：{kdj_recent_window}")

        conditions: list[str] = []
        parameters: list[Any] = []
        if query:
            conditions.append("(s.code LIKE ? OR s.name LIKE ?)")
            parameters.extend((f"{query}%", f"%{query}%"))
        if market is not None:
            conditions.append("s.market = ?")
            parameters.append(market.value)
        if macd_signal is not None:
            conditions.append("m.signal_type = ?")
            parameters.append(macd_signal)
        if macd_zero_axis is not None:
            conditions.append("m.zero_axis = ?")
            parameters.append(macd_zero_axis)
        if macd_recent_window is not None:
            if macd_signal is None:
                conditions.append("m.signal_type <> 'none'")
            conditions.append("m.recent_signal_days IS NOT NULL")
            conditions.append("m.recent_signal_days <= ?")
            parameters.append(_MACD_RECENT_WINDOWS[macd_recent_window])
        if any(value is not None for value in (kdj_signal, kdj_signal_zone, kdj_recent_window)):
            conditions.append("kdj.quality NOT IN ('error', 'insufficient')")
        if kdj_signal is not None:
            conditions.append("kdj.signal_type = ?")
            parameters.append(kdj_signal)
        elif kdj_signal_zone is not None or kdj_recent_window is not None:
            conditions.append("kdj.signal_type IN ('golden_cross', 'death_cross')")
        if kdj_signal_zone is not None:
            conditions.append("kdj.signal_zone = ?")
            parameters.append(kdj_signal_zone)
        if kdj_recent_window is not None:
            conditions.append("kdj.recent_signal_days IS NOT NULL")
            conditions.append("kdj.recent_signal_days <= ?")
            parameters.append(_KDJ_RECENT_WINDOWS[kdj_recent_window])
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * page_size

        with self.database.lock:
            connection = self.database.connection
            total = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM stock_master s
                {_MACD_JOIN}
                {_KDJ_JOIN}
                {where_clause}
                """,
                parameters,
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT {_QUOTE_COLUMNS}
                FROM stock_master s
                LEFT JOIN latest_quote q
                  ON q.market = s.market AND q.code = s.code
                {_MACD_JOIN}
                {_KDJ_JOIN}
                {where_clause}
                ORDER BY {SORT_COLUMNS[sort_by]} {normalized_order.upper()} NULLS LAST,
                         s.market ASC, s.code ASC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_size, offset],
            ).fetchall()
        return StockPage(
            items=[self._stock_quote_row(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_stock(self, market: Market, code: str) -> StockQuoteRow | None:
        with self.database.lock:
            row = self.database.connection.execute(
                f"""
                SELECT {_QUOTE_COLUMNS}
                FROM stock_master s
                LEFT JOIN latest_quote q
                  ON q.market = s.market AND q.code = s.code
                {_MACD_JOIN}
                {_KDJ_JOIN}
                WHERE s.market = ? AND s.code = ?
                """,
                (market.value, code),
            ).fetchone()
        return None if row is None else self._stock_quote_row(row)

    def market_summary(self, stale_after_seconds: int, now: datetime) -> MarketSummary:
        if stale_after_seconds < 0:
            raise ValueError("陈旧阈值不能为负数")
        with self.database.lock:
            row = self.database.connection.execute(
                """
                SELECT total, rising, falling, flat, amount,
                       CAST(market_time AS VARCHAR)
                FROM market_summary_batch
                ORDER BY finished_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                row = self.database.connection.execute(
                    """
                    SELECT
                      COUNT(*), 0, 0, 0, 0.0, NULL
                    FROM stock_master
                    """
                ).fetchone()
        last_updated_at = None if row[5] is None else datetime.fromisoformat(row[5])
        stale_before = now - timedelta(seconds=stale_after_seconds)
        return MarketSummary(
            total=row[0],
            rising=row[1],
            falling=row[2],
            flat=row[3],
            amount=float(row[4]),
            market_status="open" if self._is_market_open(now) else "closed",
            last_updated_at=last_updated_at,
            stale=last_updated_at is None or last_updated_at < stale_before,
        )

    def data_status(self) -> DataStatus:
        with self.database.lock:
            row = self.database.connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM stock_master),
                  (SELECT COUNT(*) FROM latest_quote),
                  (SELECT COUNT(*) FROM quote_snapshot_hot),
                  (SELECT COUNT(*) FROM bar_hot),
                  CAST((SELECT MAX(captured_at) FROM latest_quote) AS VARCHAR)
                """
            ).fetchone()
            latest_success = self.database.connection.execute(
                """
                SELECT CAST(finished_at AS VARCHAR), CAST(market_time AS VARCHAR)
                FROM ingestion_run
                WHERE kind = 'snapshot' AND status = 'success'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_failure = self.database.connection.execute(
                """
                SELECT CAST(finished_at AS VARCHAR)
                FROM ingestion_run
                WHERE kind = 'snapshot' AND status = 'failed'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_run = self.database.connection.execute(
                """
                SELECT expected_row_count, actual_row_count, quality_status
                FROM ingestion_run
                WHERE kind = 'snapshot'
                ORDER BY finished_at DESC NULLS LAST, started_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        expected = None if latest_run is None else latest_run[0]
        actual = None if latest_run is None else latest_run[1]
        return DataStatus(
            stock_count=row[0],
            latest_quote_count=row[1],
            snapshot_count=row[2],
            bar_count=row[3],
            latest_captured_at=None if row[4] is None else datetime.fromisoformat(row[4]),
            latest_success_at=(
                None if latest_success is None or latest_success[0] is None
                else datetime.fromisoformat(latest_success[0])
            ),
            latest_failure_at=(
                None if latest_failure is None or latest_failure[0] is None
                else datetime.fromisoformat(latest_failure[0])
            ),
            latest_market_time=(
                None if latest_success is None or latest_success[1] is None
                else datetime.fromisoformat(latest_success[1])
            ),
            snapshot_expected_count=expected,
            snapshot_actual_count=actual,
            snapshot_coverage_ratio=(
                None if expected in (None, 0) or actual is None else actual / expected
            ),
            snapshot_quality_status=None if latest_run is None else latest_run[2],
        )

    def upsert_macd(self, calculation: MacdCalculation) -> None:
        summary = calculation.summary
        point_rows = [
            (
                point.market.value,
                point.code,
                point.period,
                point.bar_time,
                point.diff,
                point.dea,
                point.histogram,
                point.signal_type.value,
                point.zero_axis.value,
                point.is_intraday,
                point.quality.value,
            )
            for point in calculation.points
        ]
        with self.database.lock:
            connection = self.database.connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO indicator_macd_latest (
                      market, code, period, calculated_at, market_time, diff,
                      dea, histogram, signal_type, signal_date,
                      recent_signal_days, recent_signal_label, zero_axis,
                      status, is_intraday, quality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary.market.value,
                        summary.code,
                        summary.period,
                        summary.calculated_at,
                        summary.market_time,
                        summary.diff,
                        summary.dea,
                        summary.histogram,
                        summary.signal_type.value,
                        summary.signal_date,
                        summary.recent_signal_days,
                        summary.recent_signal_label,
                        summary.zero_axis.value,
                        summary.status,
                        summary.is_intraday,
                        summary.quality.value,
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM indicator_macd_series
                    WHERE market = ? AND code = ? AND period = ?
                    """,
                    (summary.market.value, summary.code, summary.period),
                )
                if point_rows:
                    connection.executemany(
                        """
                        INSERT OR REPLACE INTO indicator_macd_series (
                          market, code, period, bar_time, diff, dea, histogram,
                          signal_type, zero_axis, is_intraday, quality
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        point_rows,
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_macd(self, market: Market, code: str, period: str = "1d") -> MacdCalculation | None:
        with self.database.lock:
            connection = self.database.connection
            summary_row = connection.execute(
                """
                SELECT market, code, period, CAST(calculated_at AS VARCHAR),
                       CAST(market_time AS VARCHAR), diff, dea, histogram,
                       signal_type, CAST(signal_date AS VARCHAR),
                       recent_signal_days, recent_signal_label, zero_axis,
                       status, is_intraday, quality
                FROM indicator_macd_latest
                WHERE market = ? AND code = ? AND period = ?
                """,
                (market.value, code, period),
            ).fetchone()
            if summary_row is None:
                return None
            point_rows = connection.execute(
                """
                SELECT market, code, period, CAST(bar_time AS VARCHAR),
                       diff, dea, histogram, signal_type, zero_axis,
                       is_intraday, quality
                FROM indicator_macd_series
                WHERE market = ? AND code = ? AND period = ?
                ORDER BY bar_time ASC
                """,
                (market.value, code, period),
            ).fetchall()
        summary = MacdSummary(
            market=Market(summary_row[0]),
            code=summary_row[1],
            period=summary_row[2],
            calculated_at=datetime.fromisoformat(summary_row[3]),
            market_time=None if summary_row[4] is None else datetime.fromisoformat(summary_row[4]),
            diff=summary_row[5],
            dea=summary_row[6],
            histogram=summary_row[7],
            signal_type=MacdSignal(summary_row[8]),
            signal_date=None if summary_row[9] is None else date.fromisoformat(summary_row[9]),
            recent_signal_days=summary_row[10],
            recent_signal_label=summary_row[11],
            zero_axis=ZeroAxisPosition(summary_row[12]),
            status=summary_row[13],
            is_intraday=summary_row[14],
            quality=MacdQuality(summary_row[15]),
        )
        points = tuple(
            MacdPoint(
                market=Market(row[0]),
                code=row[1],
                period=row[2],
                bar_time=datetime.fromisoformat(row[3]),
                diff=row[4],
                dea=row[5],
                histogram=row[6],
                signal_type=MacdSignal(row[7]),
                zero_axis=ZeroAxisPosition(row[8]),
                is_intraday=row[9],
                quality=MacdQuality(row[10]),
            )
            for row in point_rows
        )
        return MacdCalculation(summary=summary, points=points)

    def upsert_kdj(self, calculation: KdjCalculation) -> None:
        summary = calculation.summary
        point_rows = [
            (
                point.market.value,
                point.code,
                point.period,
                point.bar_time,
                point.k_value,
                point.d_value,
                point.j_value,
                point.signal_type.value,
                point.signal_zone.value,
                point.current_zone.value,
                point.is_intraday,
                point.quality.value,
            )
            for point in calculation.points
        ]
        with self.database.lock:
            connection = self.database.connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO indicator_kdj_latest (
                      market, code, period, calculated_at, market_time,
                      k_value, d_value, j_value, current_zone, signal_type,
                      signal_time, signal_zone, recent_signal_days,
                      recent_signal_label, status, is_intraday, quality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary.market.value,
                        summary.code,
                        summary.period,
                        summary.calculated_at,
                        summary.market_time,
                        summary.k_value,
                        summary.d_value,
                        summary.j_value,
                        summary.current_zone.value,
                        summary.signal_type.value,
                        summary.signal_time,
                        summary.signal_zone.value,
                        summary.recent_signal_days,
                        summary.recent_signal_label,
                        summary.status,
                        summary.is_intraday,
                        summary.quality.value,
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM indicator_kdj_series
                    WHERE market = ? AND code = ? AND period = ?
                    """,
                    (summary.market.value, summary.code, summary.period),
                )
                if point_rows:
                    connection.executemany(
                        """
                        INSERT INTO indicator_kdj_series (
                          market, code, period, bar_time, k_value, d_value,
                          j_value, signal_type, signal_zone, current_zone,
                          is_intraday, quality
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        point_rows,
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get_kdj(self, market: Market, code: str, period: str = "1d") -> KdjCalculation | None:
        with self.database.lock:
            connection = self.database.connection
            summary_row = connection.execute(
                """
                SELECT market, code, period, CAST(calculated_at AS VARCHAR),
                       CAST(market_time AS VARCHAR), k_value, d_value, j_value,
                       current_zone, signal_type, CAST(signal_time AS VARCHAR),
                       signal_zone, recent_signal_days, recent_signal_label,
                       status, is_intraday, quality
                FROM indicator_kdj_latest
                WHERE market = ? AND code = ? AND period = ?
                """,
                (market.value, code, period),
            ).fetchone()
            if summary_row is None:
                return None
            point_rows = connection.execute(
                """
                SELECT market, code, period, CAST(bar_time AS VARCHAR),
                       k_value, d_value, j_value, signal_type, signal_zone,
                       current_zone, is_intraday, quality
                FROM indicator_kdj_series
                WHERE market = ? AND code = ? AND period = ?
                ORDER BY bar_time ASC
                """,
                (market.value, code, period),
            ).fetchall()
        summary = KdjSummary(
            market=Market(summary_row[0]),
            code=summary_row[1],
            period=summary_row[2],
            calculated_at=datetime.fromisoformat(summary_row[3]),
            market_time=(
                None if summary_row[4] is None else datetime.fromisoformat(summary_row[4])
            ),
            k_value=summary_row[5],
            d_value=summary_row[6],
            j_value=summary_row[7],
            current_zone=KdjZone(summary_row[8]),
            signal_type=KdjSignal(summary_row[9]),
            signal_time=(
                None if summary_row[10] is None else datetime.fromisoformat(summary_row[10])
            ),
            signal_zone=KdjSignalZone(summary_row[11]),
            recent_signal_days=summary_row[12],
            recent_signal_label=summary_row[13],
            status=summary_row[14],
            is_intraday=summary_row[15],
            quality=KdjQuality(summary_row[16]),
        )
        points = tuple(
            KdjPoint(
                market=Market(row[0]),
                code=row[1],
                period=row[2],
                bar_time=datetime.fromisoformat(row[3]),
                k_value=row[4],
                d_value=row[5],
                j_value=row[6],
                signal_type=KdjSignal(row[7]),
                signal_zone=KdjSignalZone(row[8]),
                current_zone=KdjZone(row[9]),
                is_intraday=row[10],
                quality=KdjQuality(row[11]),
            )
            for row in point_rows
        )
        return KdjCalculation(summary=summary, points=points)

    def get_bars(
        self,
        market: Market,
        code: str,
        period: str,
        start: datetime,
        end: datetime,
        adjustment: str,
    ) -> list[Bar]:
        with self.database.lock:
            rows = self.database.connection.execute(
                """
                SELECT code, market, period, adjustment, CAST(bar_time AS VARCHAR),
                       open_price, high_price, low_price, close_price,
                       volume, amount, source, is_complete,
                       CAST(acquired_at AS VARCHAR), quality_status
                FROM bar_hot
                WHERE market = ? AND code = ? AND period = ?
                  AND adjustment = ? AND bar_time BETWEEN ? AND ?
                ORDER BY bar_time ASC
                """,
                (market.value, code, period, adjustment, start, end),
            ).fetchall()
        return [
            Bar(
                code=row[0],
                market=Market(row[1]),
                period=row[2],
                adjustment=row[3],
                bar_time=datetime.fromisoformat(row[4]),
                open_price=row[5],
                high_price=row[6],
                low_price=row[7],
                close_price=row[8],
                volume=row[9],
                amount=row[10],
                source=row[11],
                is_complete=row[12],
                acquired_at=datetime.fromisoformat(row[13]),
                quality_status=QualityStatus(row[14]),
            )
            for row in rows
        ]

    def snapshot_count(self) -> int:
        with self.database.lock:
            return self.database.connection.execute(
                "SELECT COUNT(*) FROM quote_snapshot_hot"
            ).fetchone()[0]

    def snapshot_count_for_date(self, trade_date: date) -> int:
        with self.database.lock:
            return self.database.connection.execute(
                """
                SELECT COUNT(*) FROM quote_snapshot_hot
                WHERE CAST(timezone('Asia/Shanghai', captured_at) AS DATE) = ?
                """,
                [trade_date],
            ).fetchone()[0]

    def pending_snapshot_dates(self, through_date: date) -> list[date]:
        with self.database.lock:
            rows = self.database.connection.execute(
                """
                SELECT DISTINCT CAST(timezone('Asia/Shanghai', captured_at) AS DATE)
                FROM quote_snapshot_hot
                WHERE CAST(timezone('Asia/Shanghai', captured_at) AS DATE) <= ?
                ORDER BY 1
                """,
                [through_date],
            ).fetchall()
        return [row[0] for row in rows]

    def copy_snapshots_to_parquet(self, trade_date: date, path: Path) -> None:
        with self.database.lock:
            table = self.database.connection.execute(
                """
                SELECT * FROM quote_snapshot_hot
                WHERE CAST(timezone('Asia/Shanghai', captured_at) AS DATE) = ?
                ORDER BY market, code, captured_at
                """,
                [trade_date],
            ).to_arrow_table()
        pq.write_table(table, path)

    @staticmethod
    def parquet_count(path: Path) -> int:
        return pq.read_metadata(path).num_rows

    def merge_snapshot_parquet(self, target: Path, temporary: Path) -> int:
        hot_table = pq.read_table(temporary)
        if not target.exists():
            return hot_table.num_rows

        existing_table = pq.read_table(target)
        with self.database.lock:
            connection = self.database.connection
            connection.register(_ARCHIVE_EXISTING_RELATION, existing_table)
            connection.register(_ARCHIVE_HOT_RELATION, hot_table)
            try:
                merged = connection.execute(
                    f"""
                    SELECT * EXCLUDE (_archive_priority, _identity_rank)
                    FROM (
                        SELECT *, ROW_NUMBER() OVER (
                            PARTITION BY market, code, captured_at
                            ORDER BY _archive_priority DESC
                        ) AS _identity_rank
                        FROM (
                            SELECT *, 0 AS _archive_priority
                            FROM {_ARCHIVE_EXISTING_RELATION}
                            UNION ALL
                            SELECT *, 1 AS _archive_priority
                            FROM {_ARCHIVE_HOT_RELATION}
                        )
                    )
                    WHERE _identity_rank = 1
                    ORDER BY market, code, captured_at
                    """
                ).to_arrow_table()
            finally:
                connection.unregister(_ARCHIVE_HOT_RELATION)
                connection.unregister(_ARCHIVE_EXISTING_RELATION)
        pq.write_table(merged, temporary)
        return merged.num_rows

    def delete_snapshots_for_date(self, trade_date: date) -> None:
        with self.database.lock:
            self.database.connection.execute(
                """
                DELETE FROM quote_snapshot_hot
                WHERE CAST(timezone('Asia/Shanghai', captured_at) AS DATE) = ?
                """,
                [trade_date],
            )

    def _transactional_executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        connection = self.database.connection
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.executemany(sql, rows)
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _quote_values(quote: QuoteSnapshot) -> tuple[Any, ...]:
        return (
            quote.market.value,
            quote.code,
            quote.name,
            quote.captured_at,
            quote.latest_price,
            quote.change_percent,
            quote.change_amount,
            quote.open_price,
            quote.high_price,
            quote.low_price,
            quote.previous_close,
            quote.volume,
            quote.amount,
            quote.turnover_rate,
            quote.total_market_cap,
            quote.source,
            quote.quality_status.value,
        )

    @staticmethod
    def _snapshot_batch(rows: list[tuple[Any, ...]]) -> pa.Table:
        columns = zip(*rows, strict=True)
        arrays = [
            pa.array(column, type=field.type)
            for column, field in zip(columns, _SNAPSHOT_SCHEMA, strict=True)
        ]
        return pa.Table.from_arrays(arrays, schema=_SNAPSHOT_SCHEMA)

    @staticmethod
    def _stock_quote_row(row: tuple[Any, ...]) -> StockQuoteRow:
        return StockQuoteRow(
            code=row[0], market=Market(row[1]), name=row[2], list_status=row[3], list_date=row[4],
            captured_at=None if row[5] is None else datetime.fromisoformat(row[5]),
            latest_price=row[6], change_percent=row[7], change_amount=row[8],
            open_price=row[9], high_price=row[10], low_price=row[11], previous_close=row[12],
            volume=row[13], amount=row[14], turnover_rate=row[15], total_market_cap=row[16],
            source=row[17], quality_status=None if row[18] is None else QualityStatus(row[18]),
            macd_signal_type=None if row[19] is None else MacdSignal(row[19]),
            macd_signal_date=None if row[20] is None else date.fromisoformat(row[20]),
            macd_recent_signal_days=row[21],
            macd_signal_label=row[22],
            macd_zero_axis=None if row[23] is None else ZeroAxisPosition(row[23]),
            macd_quality=None if row[24] is None else MacdQuality(row[24]),
            kdj_signal_type=None if row[25] is None else KdjSignal(row[25]),
            kdj_signal_time=None if row[26] is None else datetime.fromisoformat(row[26]),
            kdj_recent_signal_days=row[27],
            kdj_signal_label=row[28],
            kdj_signal_zone=None if row[29] is None else KdjSignalZone(row[29]),
            kdj_current_zone=None if row[30] is None else KdjZone(row[30]),
            kdj_quality=None if row[31] is None else KdjQuality(row[31]),
        )

    @staticmethod
    def _is_market_open(now: datetime) -> bool:
        shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        current_time = shanghai_now.time()
        return shanghai_now.weekday() < 5 and (
            time(9, 30) <= current_time <= time(11, 30)
            or time(13) <= current_time <= time(15)
        )
