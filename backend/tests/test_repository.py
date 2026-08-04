from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import duckdb
import pytest

from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock


class CountingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.history_writes = 0
        self.latest_writes = 0

    def execute(self, sql, parameters=None):
        normalized_sql = " ".join(sql.split()).lower()
        self._count_write(normalized_sql)
        if parameters is None:
            return self.connection.execute(sql)
        return self.connection.execute(sql, parameters)

    def executemany(self, sql, parameters):
        normalized_sql = " ".join(sql.split()).lower()
        self._count_write(normalized_sql)
        return self.connection.executemany(sql, parameters)

    def _count_write(self, normalized_sql):
        if "insert or replace into quote_snapshot_hot" in normalized_sql:
            self.history_writes += 1
        if "insert or replace into latest_quote" in normalized_sql:
            self.latest_writes += 1

    def __getattr__(self, name):
        return getattr(self.connection, name)


def quote(price: float, captured_at: datetime) -> QuoteSnapshot:
    return QuoteSnapshot(
        code="600519",
        market=Market.SH,
        name="贵州茅台",
        captured_at=captured_at,
        latest_price=price,
        change_percent=-2.13,
        change_amount=-28.92,
        open_price=1350.06,
        high_price=1350.94,
        low_price=1330.04,
        previous_close=1358.98,
        volume=33455,
        amount=4472998836.0,
        turnover_rate=0.27,
        total_market_cap=1670000000000.0,
        source="fixture",
        quality_status=QualityStatus.OK,
    )


def test_save_snapshot_updates_latest_and_preserves_history(repository):
    tz = ZoneInfo("Asia/Shanghai")
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.save_snapshot([quote(1331.0, datetime(2026, 8, 4, 10, 30, tzinfo=tz))])
    repository.save_snapshot([quote(1330.06, datetime(2026, 8, 4, 10, 31, tzinfo=tz))])

    page = repository.list_stocks(None, None, "code", "asc", 1, 50)
    assert page.total == 1
    assert page.items[0].latest_price == 1330.06
    assert repository.snapshot_count() == 2


def test_search_matches_code_prefix_and_name(repository):
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("000001", Market.SZ, "平安银行"),
        ]
    )
    assert repository.list_stocks("600", None, "code", "asc", 1, 50).total == 1
    assert repository.list_stocks("平安", None, "code", "asc", 1, 50).total == 1


def test_upsert_and_list_all_stocks_are_idempotent(repository):
    repository.upsert_stocks(
        [Stock("600519", Market.SH, "贵州茅台", list_date=date(2001, 8, 27))]
    )
    repository.upsert_stocks(
        [Stock("600519", Market.SH, "贵州茅台股份", list_date=date(2001, 8, 27))]
    )

    assert repository.list_all_stocks() == [
        Stock("600519", Market.SH, "贵州茅台股份", list_date=date(2001, 8, 27))
    ]


def test_get_stock_returns_latest_quote_or_none(repository):
    captured_at = datetime(2026, 8, 4, 10, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.save_snapshot([quote(1330.06, captured_at)])

    stock = repository.get_stock(Market.SH, "600519")
    assert stock is not None
    assert stock.latest_price == 1330.06
    assert stock.captured_at == captured_at
    assert repository.get_stock(Market.SZ, "600519") is None


def test_list_stocks_filters_market_paginates_and_rejects_invalid_sort(repository):
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("601318", Market.SH, "中国平安"),
            Stock("000001", Market.SZ, "平安银行"),
        ]
    )

    page = repository.list_stocks(None, Market.SH, "code", "desc", 2, 1)
    assert page.total == 2
    assert page.page == 2
    assert page.page_size == 1
    assert [stock.code for stock in page.items] == ["600519"]
    with pytest.raises(ValueError, match="排序字段"):
        repository.list_stocks(None, None, "code; DROP TABLE stock_master", "asc", 1, 50)
    with pytest.raises(ValueError, match="排序方向"):
        repository.list_stocks(None, None, "code", "sideways", 1, 50)


def test_market_summary_counts_price_direction_and_stale_quotes(repository):
    tz = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 8, 4, 10, 35, tzinfo=tz)
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("000001", Market.SZ, "平安银行"),
            Stock("430047", Market.BJ, "诺思兰德"),
        ]
    )
    captured_at = datetime(2026, 8, 4, 10, 34, tzinfo=tz)
    falling = quote(1330.06, captured_at)
    rising = replace(
        quote(10.5, captured_at),
        code="000001",
        market=Market.SZ,
        name="平安银行",
        change_percent=1.25,
    )
    flat = replace(
        quote(8.5, captured_at),
        code="430047",
        market=Market.BJ,
        name="诺思兰德",
        change_percent=0.0,
        amount=None,
    )
    repository.commit_snapshot_success(
        [falling, rising, flat],
        started_at=captured_at,
        source="fixture",
        market_time=captured_at,
        expected_row_count=3,
        quality_status="partial",
    )

    summary = repository.market_summary(stale_after_seconds=120, now=now)
    assert summary.total == 3
    assert summary.rising == 1
    assert summary.falling == 1
    assert summary.flat == 1
    assert summary.amount == falling.amount + rising.amount
    assert summary.market_status == "open"
    assert summary.last_updated_at == falling.captured_at
    assert summary.stale is False


def test_market_summary_without_quotes_is_closed_and_stale(repository):
    tz = ZoneInfo("Asia/Shanghai")
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])

    summary = repository.market_summary(
        stale_after_seconds=120,
        now=datetime(2026, 8, 8, 10, 35, tzinfo=tz),
    )

    assert summary.total == 1
    assert summary.rising == 0
    assert summary.falling == 0
    assert summary.flat == 0
    assert summary.amount == 0.0
    assert summary.market_status == "closed"
    assert summary.last_updated_at is None
    assert summary.stale is True


def test_data_status_reports_storage_counts_and_latest_capture(repository):
    tz = ZoneInfo("Asia/Shanghai")
    captured_at = datetime(2026, 8, 4, 10, 31, tzinfo=tz)
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.save_snapshot([quote(1330.06, captured_at)])

    status = repository.data_status()
    assert status.stock_count == 1
    assert status.latest_quote_count == 1
    assert status.snapshot_count == 1
    assert status.bar_count == 0
    assert status.latest_captured_at == captured_at


def test_upsert_bars_is_idempotent_and_get_bars_filters_range(repository):
    tz = ZoneInfo("Asia/Shanghai")
    first_time = datetime(2026, 8, 4, 10, 30, tzinfo=tz)
    second_time = datetime(2026, 8, 4, 10, 31, tzinfo=tz)
    first = Bar(
        code="600519",
        market=Market.SH,
        period="1m",
        adjustment="none",
        bar_time=first_time,
        open_price=1331.0,
        high_price=1332.0,
        low_price=1330.0,
        close_price=1331.5,
        volume=100,
        amount=133150.0,
        source="fixture",
    )
    replacement = replace(first, close_price=1331.8)
    second = replace(first, bar_time=second_time, close_price=1330.06)
    repository.upsert_bars([first, second])
    repository.upsert_bars([replacement])

    bars = repository.get_bars(
        Market.SH, "600519", "1m", first_time, first_time, "none"
    )
    assert bars == [replacement]


def test_save_snapshot_is_idempotent_keeps_newest_and_rolls_back_batch(repository):
    tz = ZoneInfo("Asia/Shanghai")
    newer = quote(1330.06, datetime(2026, 8, 4, 10, 31, tzinfo=tz))
    older = quote(1331.0, datetime(2026, 8, 4, 10, 30, tzinfo=tz))
    repository.upsert_stocks([Stock("600519", Market.SH, "贵州茅台")])
    repository.save_snapshot([newer])
    repository.save_snapshot([newer])
    repository.save_snapshot([older])

    assert repository.snapshot_count() == 2
    assert repository.get_stock(Market.SH, "600519").latest_price == 1330.06

    invalid = replace(newer, code="600000", source=None)
    with pytest.raises(duckdb.ConstraintException):
        repository.save_snapshot([replace(newer, code="601318"), invalid])
    assert repository.snapshot_count() == 2


def test_save_snapshot_updates_latest_with_two_set_based_writes(repository):
    tz = ZoneInfo("Asia/Shanghai")
    existing = quote(1330.06, datetime(2026, 8, 4, 10, 31, tzinfo=tz))
    repository.upsert_stocks(
        [
            Stock("600519", Market.SH, "贵州茅台"),
            Stock("000001", Market.SZ, "平安银行"),
        ]
    )
    repository.save_snapshot([existing])

    counting_connection = CountingConnection(repository.database.connection)
    repository.database.connection = counting_connection
    older = replace(existing, latest_price=1331.0, captured_at=datetime(2026, 8, 4, 10, 30, tzinfo=tz))
    newer = replace(existing, latest_price=1330.5, captured_at=datetime(2026, 8, 4, 10, 32, tzinfo=tz))
    another = replace(
        existing,
        code="000001",
        market=Market.SZ,
        name="平安银行",
        latest_price=10.5,
        open_price=10.4,
        high_price=10.6,
        low_price=10.3,
        previous_close=10.4,
    )

    repository.save_snapshot([older, newer, another])

    assert counting_connection.history_writes == 1
    assert counting_connection.latest_writes == 1
    assert repository.get_stock(Market.SH, "600519").latest_price == 1330.5
    assert repository.get_stock(Market.SZ, "000001").latest_price == 10.5
