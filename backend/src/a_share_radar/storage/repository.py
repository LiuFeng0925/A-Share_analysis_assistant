from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

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
    unchanged: int
    stale: int


@dataclass(frozen=True, slots=True)
class DataStatus:
    stock_count: int
    latest_quote_count: int
    snapshot_count: int
    bar_count: int
    latest_captured_at: datetime | None


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
    q.source, q.quality_status
"""

_QUOTE_VALUES_SQL = "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"


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
                INSERT OR REPLACE INTO stock_master
                (market, code, name, list_status, list_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def save_snapshot(self, quotes: Iterable[QuoteSnapshot]) -> None:
        rows = [self._quote_values(quote) for quote in quotes]
        if not rows:
            return
        with self.database.lock:
            connection = self.database.connection
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(
                    f"INSERT OR REPLACE INTO quote_snapshot_hot VALUES {_QUOTE_VALUES_SQL}",
                    rows,
                )
                for row in rows:
                    connection.execute(
                        f"""
                        INSERT OR REPLACE INTO latest_quote
                        SELECT * FROM (VALUES {_QUOTE_VALUES_SQL})
                        WHERE NOT EXISTS (
                            SELECT 1 FROM latest_quote WHERE market = ? AND code = ?
                        ) OR ? >= (
                            SELECT captured_at FROM latest_quote WHERE market = ? AND code = ?
                        )
                        """,
                        (*row, row[0], row[1], row[2], row[0], row[1]),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

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
            )
            for bar in bars
        ]
        if not rows:
            return
        with self.database.lock:
            self._transactional_executemany(
                """
                INSERT OR REPLACE INTO bar_hot VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> StockPage:
        if sort_by not in SORT_COLUMNS:
            raise ValueError(f"不支持的排序字段：{sort_by}")
        normalized_order = sort_order.lower()
        if normalized_order not in {"asc", "desc"}:
            raise ValueError(f"不支持的排序方向：{sort_order}")
        if page < 1 or page_size < 1:
            raise ValueError("页码和每页数量必须大于零")

        conditions: list[str] = []
        parameters: list[Any] = []
        if query:
            conditions.append("(s.code LIKE ? OR s.name LIKE ?)")
            parameters.extend((f"{query}%", f"%{query}%"))
        if market is not None:
            conditions.append("s.market = ?")
            parameters.append(market.value)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * page_size

        with self.database.lock:
            connection = self.database.connection
            total = connection.execute(
                f"SELECT COUNT(*) FROM stock_master s {where_clause}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT {_QUOTE_COLUMNS}
                FROM stock_master s
                LEFT JOIN latest_quote q
                  ON q.market = s.market AND q.code = s.code
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
                WHERE s.market = ? AND s.code = ?
                """,
                (market.value, code),
            ).fetchone()
        return None if row is None else self._stock_quote_row(row)

    def market_summary(self, stale_after_seconds: int, now: datetime) -> MarketSummary:
        if stale_after_seconds < 0:
            raise ValueError("陈旧阈值不能为负数")
        stale_before = now - timedelta(seconds=stale_after_seconds)
        with self.database.lock:
            row = self.database.connection.execute(
                """
                SELECT
                  COUNT(*),
                  COUNT(*) FILTER (WHERE q.change_percent > 0),
                  COUNT(*) FILTER (WHERE q.change_percent < 0),
                  COUNT(*) FILTER (WHERE q.change_percent = 0),
                  COUNT(*) FILTER (
                    WHERE q.captured_at IS NULL OR q.captured_at < ?
                  )
                FROM stock_master s
                LEFT JOIN latest_quote q
                  ON q.market = s.market AND q.code = s.code
                """,
                (stale_before,),
            ).fetchone()
        return MarketSummary(*row)

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
        return DataStatus(
            stock_count=row[0],
            latest_quote_count=row[1],
            snapshot_count=row[2],
            bar_count=row[3],
            latest_captured_at=None if row[4] is None else datetime.fromisoformat(row[4]),
        )

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
                       volume, amount, source, is_complete
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
            )
            for row in rows
        ]

    def snapshot_count(self) -> int:
        with self.database.lock:
            return self.database.connection.execute(
                "SELECT COUNT(*) FROM quote_snapshot_hot"
            ).fetchone()[0]

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
    def _stock_quote_row(row: tuple[Any, ...]) -> StockQuoteRow:
        return StockQuoteRow(
            code=row[0], market=Market(row[1]), name=row[2], list_status=row[3], list_date=row[4],
            captured_at=None if row[5] is None else datetime.fromisoformat(row[5]),
            latest_price=row[6], change_percent=row[7], change_amount=row[8],
            open_price=row[9], high_price=row[10], low_price=row[11], previous_close=row[12],
            volume=row[13], amount=row[14], turnover_rate=row[15], total_market_cap=row[16],
            source=row[17], quality_status=None if row[18] is None else QualityStatus(row[18]),
        )
