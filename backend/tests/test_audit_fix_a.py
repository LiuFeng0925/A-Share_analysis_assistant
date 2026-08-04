import asyncio
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from threading import Event
from zoneinfo import ZoneInfo

import duckdb
import pytest

import a_share_radar.main as main_module
import a_share_radar.storage.archive as archive_module
from a_share_radar.config import Settings
from a_share_radar.domain.models import Market, QualityStatus, QuoteSnapshot, Stock
from a_share_radar.services.snapshot_collector import (
    SnapshotCollector,
    SnapshotValidationError,
)
from a_share_radar.storage.database import Database

SHANGHAI = ZoneInfo("Asia/Shanghai")
CAPTURED_AT = datetime(2026, 8, 4, 10, 31, tzinfo=SHANGHAI)


def make_stock(index: int) -> Stock:
    return Stock(f"{index:06d}", Market.SH, f"样本股票{index}")


def make_quote(index: int, **changes) -> QuoteSnapshot:
    base = QuoteSnapshot(
        code=f"{index:06d}",
        market=Market.SH,
        name=f"样本股票{index}",
        captured_at=CAPTURED_AT,
        latest_price=10.0,
        change_percent=1.0,
        change_amount=0.1,
        open_price=9.9,
        high_price=10.1,
        low_price=9.8,
        previous_close=9.9,
        volume=100,
        amount=1000.0,
        turnover_rate=0.5,
        total_market_cap=100000.0,
        source="fixture",
        quality_status=QualityStatus.OK,
    )
    return replace(base, **changes)


def ingestion_columns(database: Database) -> set[str]:
    return {
        row[0]
        for row in database.connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'ingestion_run'"
        ).fetchall()
    }


def commit_success(repository, quotes, *, started_at=CAPTURED_AT, expected_count=None):
    rows = list(quotes)
    return repository.commit_snapshot_success(
        rows,
        started_at=started_at,
        source=rows[0].source,
        market_time=rows[0].captured_at,
        expected_row_count=expected_count or len(rows),
        quality_status=(
            "partial"
            if any(row.quality_status != QualityStatus.OK for row in rows)
            else "ok"
        ),
    )


def test_database_migrates_existing_ingestion_table_without_losing_rows(tmp_path):
    path = tmp_path / "旧版.duckdb"
    legacy = duckdb.connect(str(path))
    legacy.execute(
        """
        CREATE TABLE ingestion_run (
          run_id UUID PRIMARY KEY,
          kind VARCHAR NOT NULL,
          started_at TIMESTAMPTZ NOT NULL,
          finished_at TIMESTAMPTZ,
          source VARCHAR NOT NULL,
          row_count BIGINT NOT NULL DEFAULT 0,
          status VARCHAR NOT NULL,
          error_message VARCHAR
        )
        """
    )
    legacy.execute(
        """
        INSERT INTO ingestion_run VALUES (
          uuid(), 'snapshot', ?, ?, 'fixture', 2, 'success', NULL
        )
        """,
        [CAPTURED_AT, CAPTURED_AT],
    )
    legacy.close()

    database = Database(path)
    try:
        assert {
            "market_time",
            "expected_row_count",
            "actual_row_count",
            "quality_status",
        } <= ingestion_columns(database)
        assert database.connection.execute(
            "SELECT kind, row_count FROM ingestion_run"
        ).fetchall() == [("snapshot", 2)]
        assert database.connection.execute(
            """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name = 'market_summary_batch'
            """
        ).fetchone()[0] == 1
    finally:
        database.close()


async def test_collector_rejects_more_than_fixed_floor_when_master_coverage_is_low(
    repository, fake_source
):
    repository.upsert_stocks(make_stock(index) for index in range(5000))
    fake_source.snapshot_rows = [make_quote(index) for index in range(4100)]
    collector = SnapshotCollector(
        fake_source,
        repository,
        minimum_expected_count=4000,
        minimum_coverage_ratio=0.9,
    )

    with pytest.raises(SnapshotValidationError, match="覆盖率"):
        await collector.collect_once(CAPTURED_AT)

    assert repository.snapshot_count() == 0


async def test_collector_uses_recent_successful_batch_as_coverage_baseline(
    repository, fake_source
):
    repository.upsert_stocks(make_stock(index) for index in range(4100))
    repository.record_ingestion_run(
        kind="snapshot",
        started_at=CAPTURED_AT - timedelta(minutes=1),
        finished_at=CAPTURED_AT - timedelta(minutes=1),
        source="fixture",
        market_time=CAPTURED_AT - timedelta(minutes=1),
        expected_row_count=5000,
        actual_row_count=5000,
        status="success",
        quality_status="ok",
    )
    fake_source.snapshot_rows = [make_quote(index) for index in range(4100)]
    collector = SnapshotCollector(
        fake_source,
        repository,
        minimum_expected_count=4000,
        minimum_coverage_ratio=0.9,
    )

    with pytest.raises(SnapshotValidationError, match="预期 5000"):
        await collector.collect_once(CAPTURED_AT)


@pytest.mark.parametrize(
    ("quotes", "message"),
    [
        ([make_quote(0), make_quote(1, code="999999")], "未知股票"),
        ([make_quote(0), make_quote(1, volume=-1)], "非负"),
        ([make_quote(0), make_quote(1, latest_price=float("nan"))], "有限"),
        ([make_quote(0), make_quote(1, high_price=9.0)], "OHLC"),
        (
            [
                make_quote(0),
                make_quote(1, captured_at=CAPTURED_AT + timedelta(minutes=1)),
            ],
            "市场时间不统一",
        ),
        (
            [make_quote(0), make_quote(1, captured_at=CAPTURED_AT.replace(tzinfo=None))],
            "时区",
        ),
    ],
)
async def test_collector_rejects_invalid_snapshot_fields(
    repository, fake_source, quotes, message
):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    fake_source.snapshot_rows = quotes
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match=message):
        await collector.collect_once(CAPTURED_AT)

    assert repository.snapshot_count() == 0


async def test_collector_rejects_unreasonable_market_time(repository, fake_source):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    fake_source.snapshot_rows = [
        make_quote(0, captured_at=CAPTURED_AT - timedelta(minutes=20)),
        make_quote(1, captured_at=CAPTURED_AT - timedelta(minutes=20)),
    ]
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match="市场时间异常"):
        await collector.collect_once(CAPTURED_AT)


async def test_validation_failure_records_candidate_market_time(repository, fake_source):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    fake_source.snapshot_rows = [make_quote(0)]
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    with pytest.raises(SnapshotValidationError, match="行情数量异常"):
        await collector.collect_once(CAPTURED_AT)

    row = repository.database.connection.execute(
        """
        SELECT CAST(market_time AS VARCHAR), status
        FROM ingestion_run
        """
    ).fetchone()
    assert datetime.fromisoformat(row[0]) == CAPTURED_AT
    assert row[1] == "failed"


async def test_preflight_failure_is_also_recorded(repository, fake_source, monkeypatch):
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)

    def fail_expectation(minimum_expected_count):
        raise RuntimeError("模拟预检失败")

    monkeypatch.setattr(repository, "snapshot_expectation", fail_expectation)

    with pytest.raises(RuntimeError, match="模拟预检失败"):
        await collector.collect_once(CAPTURED_AT)

    row = repository.database.connection.execute(
        """
        SELECT status, expected_row_count, actual_row_count, error_message
        FROM ingestion_run
        """
    ).fetchone()
    assert row == ("failed", 2, 0, "行情采集预检失败")
    assert fake_source.snapshot_requests == 0


def test_partial_snapshot_is_auditable_without_erasing_latest_valid_fields(repository):
    repository.upsert_stocks([make_stock(0)])
    repository.save_snapshot([make_quote(0)])
    partial_time = CAPTURED_AT + timedelta(minutes=1)
    repository.save_snapshot(
        [
            make_quote(
                0,
                captured_at=partial_time,
                latest_price=None,
                amount=None,
                quality_status=QualityStatus.PARTIAL,
            )
        ]
    )

    current = repository.get_stock(Market.SH, "000000")
    assert repository.snapshot_count() == 2
    assert current is not None
    assert current.captured_at == CAPTURED_AT
    assert current.latest_price == 10.0
    assert current.amount == 1000.0
    assert current.high_price == 10.1
    assert current.low_price == 9.8
    assert current.source == "fixture"
    assert current.quality_status == QualityStatus.OK


def test_partial_high_low_never_mix_with_old_price_provenance(repository):
    repository.upsert_stocks([make_stock(0)])
    repository.save_snapshot([make_quote(0)])
    partial_time = CAPTURED_AT + timedelta(minutes=1)
    repository.save_snapshot(
        [
            make_quote(
                0,
                captured_at=partial_time,
                latest_price=None,
                open_price=19.5,
                high_price=20.0,
                low_price=19.0,
                amount=2000.0,
                quality_status=QualityStatus.PARTIAL,
                source="partial-source",
            )
        ]
    )

    current = repository.get_stock(Market.SH, "000000")

    assert current is not None
    assert current.captured_at == CAPTURED_AT
    assert current.latest_price == 10.0
    assert current.open_price == 9.9
    assert current.high_price == 10.1
    assert current.low_price == 9.8
    assert current.amount == 1000.0
    assert current.source == "fixture"
    assert current.quality_status == QualityStatus.OK


def test_ok_row_missing_required_field_does_not_advance_latest(repository):
    repository.upsert_stocks([make_stock(0)])
    repository.save_snapshot([make_quote(0)])
    repository.save_snapshot(
        [
            make_quote(
                0,
                captured_at=CAPTURED_AT + timedelta(minutes=1),
                amount=None,
                quality_status=QualityStatus.OK,
            )
        ]
    )

    current = repository.get_stock(Market.SH, "000000")

    assert current is not None
    assert current.captured_at == CAPTURED_AT
    assert current.amount == 1000.0


def test_market_summary_uses_one_recent_successful_batch(repository):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    first_batch = [make_quote(0), make_quote(1, change_percent=-1.0, amount=2000.0)]
    commit_success(repository, first_batch)
    later = CAPTURED_AT + timedelta(minutes=1)
    repository.save_snapshot(
        [make_quote(0, captured_at=later, change_percent=2.0, amount=9999.0)]
    )
    repository.record_ingestion_run(
        kind="snapshot",
        started_at=later,
        finished_at=later,
        source="fixture",
        market_time=later,
        expected_row_count=2,
        actual_row_count=1,
        status="failed",
        quality_status="error",
        error_message="行情覆盖率不足",
    )

    summary = repository.market_summary(120, later)

    assert summary.last_updated_at == CAPTURED_AT
    assert summary.total == 2
    assert summary.rising == 1
    assert summary.falling == 1
    assert summary.amount == 3000.0


def test_market_summary_survives_hot_snapshot_archival(repository, tmp_path):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    commit_success(
        repository,
        [make_quote(0), make_quote(1, change_percent=-1.0, amount=2000.0)],
    )
    before = repository.market_summary(120, CAPTURED_AT)

    archive_module.archive_snapshots(repository, CAPTURED_AT.date(), tmp_path)
    after = repository.market_summary(120, CAPTURED_AT)

    assert repository.snapshot_count() == 0
    assert after == before
    assert after.amount == 3000.0


def test_market_summary_without_success_never_falls_back_to_mixed_latest(repository):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    repository.save_snapshot([make_quote(0)])

    summary = repository.market_summary(120, CAPTURED_AT)

    assert summary.total == 2
    assert summary.rising == 0
    assert summary.falling == 0
    assert summary.flat == 0
    assert summary.amount == 0.0
    assert summary.last_updated_at is None
    assert summary.stale is True


class FailingConnection:
    def __init__(self, connection, failure_marker):
        self.connection = connection
        self.failure_marker = failure_marker

    def execute(self, sql, parameters=None):
        normalized = " ".join(sql.split()).lower()
        if self.failure_marker in normalized:
            raise RuntimeError(f"模拟事务步骤失败：{self.failure_marker}")
        if parameters is None:
            return self.connection.execute(sql)
        return self.connection.execute(sql, parameters)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@pytest.mark.parametrize(
    "failure_marker",
    ["insert into ingestion_run", "insert into market_summary_batch"],
)
def test_atomic_snapshot_commit_rolls_back_every_step_on_failure(
    repository, failure_marker
):
    repository.upsert_stocks([Stock("000000", Market.SH, "旧名称")])
    repository.database.connection = FailingConnection(
        repository.database.connection, failure_marker
    )

    with pytest.raises(RuntimeError, match="模拟事务步骤失败"):
        commit_success(repository, [make_quote(0, name="新名称")])

    assert repository.snapshot_count() == 0
    assert repository.get_stock(Market.SH, "000000").captured_at is None
    assert repository.list_all_stocks() == [Stock("000000", Market.SH, "旧名称")]
    assert repository.database.connection.execute(
        "SELECT COUNT(*) FROM ingestion_run"
    ).fetchone()[0] == 0
    assert repository.database.connection.execute(
        "SELECT COUNT(*) FROM market_summary_batch"
    ).fetchone()[0] == 0


def test_atomic_snapshot_commit_persists_all_outputs_and_real_finish_time(repository):
    repository.upsert_stocks([Stock("000000", Market.SH, "旧名称")])
    before = datetime.now(UTC)

    result = commit_success(repository, [make_quote(0, name="新名称")])

    after = datetime.now(UTC)
    run = repository.database.connection.execute(
        """
        SELECT CAST(finished_at AS VARCHAR), status
        FROM ingestion_run WHERE run_id = ?
        """,
        [str(result.run_id)],
    ).fetchone()
    summary_count = repository.database.connection.execute(
        "SELECT COUNT(*) FROM market_summary_batch WHERE run_id = ?",
        [str(result.run_id)],
    ).fetchone()[0]
    summary_row = repository.database.connection.execute(
        """
        SELECT CAST(market_time AS VARCHAR), total, rising, falling, flat,
               amount, source, quality_status
        FROM market_summary_batch WHERE run_id = ?
        """,
        [str(result.run_id)],
    ).fetchone()
    finished_at = datetime.fromisoformat(run[0])
    assert before <= finished_at <= after
    assert finished_at.tzinfo is not None
    assert result.finished_at == finished_at
    assert run[1] == "success"
    assert summary_count == 1
    assert datetime.fromisoformat(summary_row[0]) == CAPTURED_AT
    assert summary_row[1:] == (1, 1, 0, 0, 1000.0, "fixture", "ok")
    assert repository.snapshot_count() == 1
    assert repository.get_stock(Market.SH, "000000").latest_price == 10.0
    assert repository.list_all_stocks() == [Stock("000000", Market.SH, "新名称")]


async def test_collector_records_success_and_sanitized_failure(repository, fake_source):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    fake_source.snapshot_rows = [make_quote(0), make_quote(1)]
    collector = SnapshotCollector(
        fake_source, repository, minimum_expected_count=2, retry_delays=()
    )

    await collector.collect_once(CAPTURED_AT)
    fake_source.snapshot_failures = 1
    with pytest.raises(RuntimeError, match="模拟上游瞬时失败"):
        await collector.collect_once(CAPTURED_AT + timedelta(minutes=1))

    rows = repository.database.connection.execute(
        """
        SELECT status, quality_status, expected_row_count, actual_row_count,
               error_message
        FROM ingestion_run ORDER BY started_at
        """
    ).fetchall()
    assert rows[0][:4] == ("success", "ok", 2, 2)
    assert rows[1][:4] == ("failed", "error", 2, 0)
    assert rows[1][4] == "上游行情获取失败"
    assert "模拟上游" not in rows[1][4]


async def test_collector_success_uses_only_atomic_repository_commit(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)
    original_commit = repository.commit_snapshot_success
    calls = 0

    def recording_commit(quotes, **kwargs):
        nonlocal calls
        calls += 1
        return original_commit(quotes, **kwargs)

    def forbidden_legacy_call(*args, **kwargs):
        raise AssertionError("成功路径不得拆分提交")

    monkeypatch.setattr(repository, "commit_snapshot_success", recording_commit)
    monkeypatch.setattr(repository, "save_snapshot", forbidden_legacy_call)
    monkeypatch.setattr(repository, "refresh_stock_names", forbidden_legacy_call)
    original_record = repository.record_ingestion_run

    def failure_only_record(**kwargs):
        if kwargs["status"] == "success":
            raise AssertionError("成功审计必须位于原子事务内")
        return original_record(**kwargs)

    monkeypatch.setattr(repository, "record_ingestion_run", failure_only_record)

    await collector.collect_once(CAPTURED_AT)

    assert calls == 1
    assert repository.snapshot_count() == 2
    assert repository.database.connection.execute(
        "SELECT status FROM ingestion_run"
    ).fetchall() == [("success",)]


def test_data_status_includes_recent_batch_coverage_and_quality(repository):
    repository.upsert_stocks([make_stock(0), make_stock(1)])
    result = commit_success(repository, [make_quote(0), make_quote(1)])

    status = repository.data_status()

    assert status.latest_success_at == result.finished_at
    assert status.latest_failure_at is None
    assert status.latest_market_time == CAPTURED_AT
    assert status.snapshot_expected_count == 2
    assert status.snapshot_actual_count == 2
    assert status.snapshot_coverage_ratio == 1.0
    assert status.snapshot_quality_status == "ok"


async def test_maintenance_retries_master_and_calendar_without_forgetting_cache(
    monkeypatch, repository, fake_source
):
    cached_day = date(2026, 8, 4)
    repository.replace_trading_days({cached_day})
    repository.upsert_stocks([make_stock(0)])
    fake_source.stock_error = RuntimeError("首次主数据失败")
    fake_source.trading_day_error = RuntimeError("首次日历失败")
    fixed_now = datetime(2026, 8, 5, 12, 0, tzinfo=SHANGHAI)
    monkeypatch.setattr(main_module, "datetime", type(
        "FixedDateTime",
        (datetime,),
        {"now": classmethod(lambda cls, tz=None: fixed_now)},
    ))
    app = main_module.create_app(source=fake_source, database=repository.database)

    async with app.router.lifespan_context(app):
        assert app.state.clock.trading_days == {cached_day}
        fake_source.stock_error = None
        fake_source.trading_day_error = None
        fake_source.stock_rows = [make_stock(0), Stock("000001", Market.SZ, "新股")]
        maintenance_job = next(
            job
            for job in app.state.scheduler.get_jobs()
            if job.func.__name__ == "maintenance_managed"
        )
        await maintenance_job.func()

        assert Stock("000001", Market.SZ, "新股") in repository.list_all_stocks()
        assert app.state.clock.trading_days == {cached_day}
        assert len(fake_source.trading_day_requests) >= 2


async def test_empty_calendar_response_preserves_recent_valid_cache(
    monkeypatch, repository, fake_source
):
    cached_day = date(2026, 8, 4)
    repository.replace_trading_days({cached_day})

    async def fetch_empty_calendar(start, end):
        return set()

    monkeypatch.setattr(fake_source, "fetch_trading_days", fetch_empty_calendar)
    fixed_now = datetime(2026, 8, 5, 12, 0, tzinfo=SHANGHAI)
    monkeypatch.setattr(
        main_module,
        "datetime",
        type(
            "FixedDateTime",
            (datetime,),
            {"now": classmethod(lambda cls, tz=None: fixed_now)},
        ),
    )
    app = main_module.create_app(source=fake_source, database=repository.database)

    async with app.router.lifespan_context(app):
        assert app.state.clock.trading_days == {cached_day}


async def test_lifespan_archives_previous_hot_dates_during_startup(
    monkeypatch, repository, fake_source, tmp_path
):
    old_quote = replace(
        fake_source.snapshot_rows[0],
        captured_at=datetime(2026, 8, 3, 14, 59, tzinfo=SHANGHAI),
    )
    repository.save_snapshot([old_quote])
    fixed_now = datetime(2026, 8, 4, 12, 0, tzinfo=SHANGHAI)
    monkeypatch.setattr(
        main_module,
        "datetime",
        type(
            "FixedDateTime",
            (datetime,),
            {"now": classmethod(lambda cls, tz=None: fixed_now)},
        ),
    )
    settings = Settings(data_dir=tmp_path / "启动补归档")
    app = main_module.create_app(
        settings=settings, source=fake_source, database=repository.database
    )

    async with app.router.lifespan_context(app):
        assert repository.snapshot_count_for_date(date(2026, 8, 3)) == 0

    assert (
        settings.data_dir
        / "snapshots"
        / "trade_date=2026-08-03"
        / "part-000.parquet"
    ).exists()


def test_archive_pending_snapshots_catches_up_and_retries_failures(
    monkeypatch, repository, fake_source, tmp_path
):
    previous_day = date(2026, 8, 3)
    old_quote = replace(
        fake_source.snapshot_rows[0],
        captured_at=datetime(2026, 8, 3, 14, 59, tzinfo=SHANGHAI),
    )
    repository.save_snapshot([old_quote])
    original_copy = repository.copy_snapshots_to_parquet
    attempts = 0

    def fail_once(trade_date, path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("模拟归档失败")
        original_copy(trade_date, path)

    monkeypatch.setattr(repository, "copy_snapshots_to_parquet", fail_once)

    after_close = datetime(2026, 8, 3, 15, 10, tzinfo=SHANGHAI)
    failures = archive_module.archive_pending_snapshots(
        repository, tmp_path, after_close
    )
    assert failures == {previous_day: "模拟归档失败"}
    assert repository.snapshot_count_for_date(previous_day) == 1

    failures = archive_module.archive_pending_snapshots(
        repository, tmp_path, after_close
    )
    assert failures == {}
    assert repository.snapshot_count_for_date(previous_day) == 0
    assert (
        tmp_path
        / "snapshots"
        / "trade_date=2026-08-03"
        / "part-000.parquet"
    ).exists()


def test_archive_pending_snapshots_never_archives_today_before_1510(
    repository, fake_source, tmp_path
):
    today = date(2026, 8, 4)
    repository.save_snapshot(fake_source.snapshot_rows[:1])

    archive_module.archive_pending_snapshots(
        repository,
        tmp_path,
        datetime(2026, 8, 4, 12, 0, tzinfo=SHANGHAI),
    )

    target = tmp_path / "snapshots" / "trade_date=2026-08-04" / "part-000.parquet"
    assert target.exists() is False
    assert repository.snapshot_count_for_date(today) == 1

    archive_module.archive_pending_snapshots(
        repository,
        tmp_path,
        datetime(2026, 8, 4, 15, 10, tzinfo=SHANGHAI),
    )
    assert target.exists()
    assert repository.snapshot_count_for_date(today) == 0


async def test_snapshot_write_does_not_block_event_loop(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)
    original_commit = repository.commit_snapshot_success

    def slow_commit(quotes, **kwargs):
        time.sleep(0.15)
        return original_commit(quotes, **kwargs)

    monkeypatch.setattr(repository, "commit_snapshot_success", slow_commit)
    loop = asyncio.get_running_loop()
    started = loop.time()
    heartbeat_delay = None

    async def heartbeat():
        nonlocal heartbeat_delay
        await asyncio.sleep(0.01)
        heartbeat_delay = loop.time() - started

    heartbeat_task = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    await collector.collect_once(CAPTURED_AT)
    await heartbeat_task

    assert heartbeat_delay is not None
    assert heartbeat_delay < 0.08


async def test_cancellation_waits_for_snapshot_write_before_propagating(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)
    original_commit = repository.commit_snapshot_success
    write_finished = Event()

    def slow_commit(quotes, **kwargs):
        time.sleep(0.1)
        result = original_commit(quotes, **kwargs)
        write_finished.set()
        return result

    monkeypatch.setattr(repository, "commit_snapshot_success", slow_commit)
    task = asyncio.create_task(collector.collect_once(CAPTURED_AT))
    asyncio.get_running_loop().call_later(0.02, task.cancel)

    with pytest.raises(asyncio.CancelledError):
        await task

    assert write_finished.is_set()
    assert repository.snapshot_count() == 2
    assert repository.database.connection.execute(
        "SELECT status FROM ingestion_run"
    ).fetchall() == [("success",)]


async def test_cancellation_during_atomic_commit_does_not_add_false_failure(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)
    original_commit = repository.commit_snapshot_success
    commit_started = Event()

    def slow_commit(quotes, **kwargs):
        commit_started.set()
        time.sleep(0.1)
        return original_commit(quotes, **kwargs)

    monkeypatch.setattr(repository, "commit_snapshot_success", slow_commit)
    task = asyncio.create_task(collector.collect_once(CAPTURED_AT))
    assert await asyncio.to_thread(commit_started.wait, 1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    statuses = repository.database.connection.execute(
        "SELECT status FROM ingestion_run ORDER BY status"
    ).fetchall()
    assert statuses == [("success",)]


async def test_cancellation_during_failed_atomic_commit_records_only_failure(
    monkeypatch, repository, fake_source
):
    repository.upsert_stocks(fake_source.stock_rows)
    collector = SnapshotCollector(fake_source, repository, minimum_expected_count=2)
    commit_started = Event()

    def slow_failed_commit(quotes, **kwargs):
        commit_started.set()
        time.sleep(0.1)
        raise RuntimeError("模拟原子提交失败")

    monkeypatch.setattr(
        repository, "commit_snapshot_success", slow_failed_commit
    )
    task = asyncio.create_task(collector.collect_once(CAPTURED_AT))
    assert await asyncio.to_thread(commit_started.wait, 1)
    task.cancel()

    with pytest.raises(RuntimeError, match="模拟原子提交失败"):
        await task

    assert repository.snapshot_count() == 0
    assert repository.database.connection.execute(
        "SELECT status, error_message FROM ingestion_run"
    ).fetchall() == [("failed", "行情存储失败")]
