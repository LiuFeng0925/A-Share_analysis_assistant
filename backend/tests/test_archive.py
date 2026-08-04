from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from threading import Event

import duckdb
import pytest

from a_share_radar.storage.archive import archive_snapshots

TRADE_DATE = date(2026, 8, 4)


def archived_count(path):
    return duckdb.sql(
        "SELECT count(*) FROM read_parquet(?)", params=[str(path)]
    ).fetchone()[0]


def test_archive_writes_verified_parquet_and_removes_hot_rows(
    repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])

    output = archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert archived_count(output) == 1
    assert repository.snapshot_count_for_date(TRADE_DATE) == 0


def test_archive_validation_failure_preserves_hot_rows_and_formal_file(
    monkeypatch, repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    target = archive_snapshots(repository, TRADE_DATE, tmp_path)
    late = replace(
        fake_source.snapshot_rows[0],
        captured_at=fake_source.snapshot_rows[0].captured_at + timedelta(minutes=1),
        latest_price=1329.0,
    )
    repository.save_snapshot([late])
    original_contents = target.read_bytes()
    monkeypatch.setattr(repository, "parquet_count", lambda path: 0)

    with pytest.raises(RuntimeError, match="归档校验失败"):
        archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert repository.snapshot_count_for_date(TRADE_DATE) == 1
    assert target.read_bytes() == original_contents


def test_archive_replaces_file_before_deleting_hot_rows(
    monkeypatch, repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    target = (
        tmp_path
        / "snapshots"
        / "trade_date=2026-08-04"
        / "part-000.parquet"
    )
    observations: list[tuple[bool, int]] = []
    original_delete = repository.delete_snapshots_for_date

    def observe_delete(trade_date):
        count = duckdb.sql(
            "SELECT count(*) FROM read_parquet(?)", params=[str(target)]
        ).fetchone()[0]
        observations.append((target.exists(), count))
        original_delete(trade_date)

    monkeypatch.setattr(repository, "delete_snapshots_for_date", observe_delete)

    archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert observations == [(True, 1)]
    assert repository.snapshot_count_for_date(TRADE_DATE) == 0


def test_archive_supports_safe_paths_containing_quotes(repository, tmp_path, fake_source):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    data_dir = tmp_path / "含'引号"

    output = archive_snapshots(repository, TRADE_DATE, data_dir)

    assert output.exists()
    assert repository.parquet_count(output) == 1


def test_rearchive_merges_existing_archive_with_late_hot_rows(
    repository, tmp_path, fake_source
):
    first = fake_source.snapshot_rows[0]
    repository.save_snapshot([first])
    target = archive_snapshots(repository, TRADE_DATE, tmp_path)
    late = replace(
        first,
        captured_at=first.captured_at + timedelta(minutes=1),
        latest_price=1329.0,
    )
    repository.save_snapshot([late])

    output = archive_snapshots(repository, TRADE_DATE, tmp_path)

    rows = duckdb.sql(
        """
        SELECT market, code, CAST(captured_at AS VARCHAR), latest_price
        FROM read_parquet(?) ORDER BY captured_at
        """,
        params=[str(output)],
    ).fetchall()
    assert output == target
    assert len(rows) == 2
    assert [row[3] for row in rows] == [first.latest_price, late.latest_price]
    assert repository.snapshot_count_for_date(TRADE_DATE) == 0


def test_existing_archive_without_new_hot_rows_is_returned_unchanged(
    repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    target = archive_snapshots(repository, TRADE_DATE, tmp_path)
    original_contents = target.read_bytes()

    output = archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert output == target
    assert target.read_bytes() == original_contents


def test_archive_holds_repository_lock_until_hot_rows_are_deleted(
    monkeypatch, repository, tmp_path, fake_source
):
    first = fake_source.snapshot_rows[0]
    late = replace(
        first,
        captured_at=first.captured_at + timedelta(minutes=1),
        latest_price=1329.0,
    )
    repository.save_snapshot([first])
    verification_started = Event()
    release_verification = Event()
    writer_attempted = Event()
    writer_done = Event()
    original_count = repository.parquet_count

    def pausing_count(path):
        verification_started.set()
        assert release_verification.wait(2)
        return original_count(path)

    def write_late_snapshot():
        writer_attempted.set()
        repository.save_snapshot([late])
        writer_done.set()

    monkeypatch.setattr(repository, "parquet_count", pausing_count)
    with ThreadPoolExecutor(max_workers=2) as executor:
        archiving = executor.submit(
            archive_snapshots, repository, TRADE_DATE, tmp_path
        )
        assert verification_started.wait(2)
        writing = executor.submit(write_late_snapshot)
        assert writer_attempted.wait(2)
        assert writer_done.wait(0.1) is False
        release_verification.set()
        target = archiving.result(timeout=2)
        writing.result(timeout=2)

    assert archived_count(target) == 1
    assert repository.snapshot_count_for_date(TRADE_DATE) == 1
    monkeypatch.setattr(repository, "parquet_count", original_count)
    archive_snapshots(repository, TRADE_DATE, tmp_path)
    assert archived_count(target) == 2
    assert repository.snapshot_count_for_date(TRADE_DATE) == 0


def test_concurrent_archives_use_unique_temporary_paths(
    monkeypatch, repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    temporary_paths = []
    original_copy = repository.copy_snapshots_to_parquet

    def record_copy(trade_date, path):
        temporary_paths.append(path)
        original_copy(trade_date, path)

    def fail_delete(trade_date):
        raise RuntimeError("模拟删除失败")

    monkeypatch.setattr(repository, "copy_snapshots_to_parquet", record_copy)
    monkeypatch.setattr(repository, "delete_snapshots_for_date", fail_delete)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(archive_snapshots, repository, TRADE_DATE, tmp_path)
            for _ in range(2)
        ]
        for future in futures:
            with pytest.raises(RuntimeError, match="模拟删除失败"):
                future.result(timeout=2)

    assert len(temporary_paths) == 2
    assert len(set(temporary_paths)) == 2
    assert all(path.exists() is False for path in temporary_paths)


def test_metadata_failure_cleans_unique_temporary_and_preserves_data(
    monkeypatch, repository, tmp_path, fake_source
):
    first = fake_source.snapshot_rows[0]
    repository.save_snapshot([first])
    target = archive_snapshots(repository, TRADE_DATE, tmp_path)
    original_contents = target.read_bytes()
    late = replace(first, captured_at=first.captured_at + timedelta(minutes=1))
    repository.save_snapshot([late])
    temporary_paths = []
    original_copy = repository.copy_snapshots_to_parquet

    def record_copy(trade_date, path):
        temporary_paths.append(path)
        original_copy(trade_date, path)

    def fail_metadata(path):
        raise RuntimeError("模拟元数据读取失败")

    monkeypatch.setattr(repository, "copy_snapshots_to_parquet", record_copy)
    monkeypatch.setattr(repository, "parquet_count", fail_metadata)

    with pytest.raises(RuntimeError, match="模拟元数据读取失败"):
        archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert len(temporary_paths) == 1
    assert temporary_paths[0].exists() is False
    assert target.read_bytes() == original_contents
    assert repository.snapshot_count_for_date(TRADE_DATE) == 1


def test_delete_failure_after_replace_keeps_recoverable_duplicate(
    monkeypatch, repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    original_delete = repository.delete_snapshots_for_date

    def fail_delete(trade_date):
        raise RuntimeError("模拟删除失败")

    monkeypatch.setattr(repository, "delete_snapshots_for_date", fail_delete)
    with pytest.raises(RuntimeError, match="模拟删除失败"):
        archive_snapshots(repository, TRADE_DATE, tmp_path)
    target = tmp_path / "snapshots" / "trade_date=2026-08-04" / "part-000.parquet"
    assert archived_count(target) == 1
    assert repository.snapshot_count_for_date(TRADE_DATE) == 1

    monkeypatch.setattr(repository, "delete_snapshots_for_date", original_delete)
    archive_snapshots(repository, TRADE_DATE, tmp_path)

    assert archived_count(target) == 1
    assert repository.snapshot_count_for_date(TRADE_DATE) == 0
