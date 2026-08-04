from datetime import date

import duckdb
import pytest

from a_share_radar.storage.archive import archive_snapshots


def test_archive_writes_verified_parquet_and_removes_hot_rows(
    repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])

    output = archive_snapshots(repository, date(2026, 8, 4), tmp_path)

    archived = duckdb.sql(
        "SELECT count(*) FROM read_parquet(?)", params=[str(output)]
    ).fetchone()[0]
    assert archived == 1
    assert repository.snapshot_count_for_date(date(2026, 8, 4)) == 0


def test_archive_validation_failure_preserves_hot_rows_and_formal_file(
    monkeypatch, repository, tmp_path, fake_source
):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    target = (
        tmp_path
        / "snapshots"
        / "trade_date=2026-08-04"
        / "part-000.parquet"
    )
    target.parent.mkdir(parents=True)
    original_contents = "原正式文件".encode()
    target.write_bytes(original_contents)
    monkeypatch.setattr(repository, "parquet_count", lambda path: 0)

    with pytest.raises(RuntimeError, match="归档校验失败"):
        archive_snapshots(repository, date(2026, 8, 4), tmp_path)

    assert repository.snapshot_count_for_date(date(2026, 8, 4)) == 1
    assert target.read_bytes() == original_contents
    assert target.with_suffix(".parquet.tmp").exists() is False


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

    archive_snapshots(repository, date(2026, 8, 4), tmp_path)

    assert observations == [(True, 1)]
    assert repository.snapshot_count_for_date(date(2026, 8, 4)) == 0


def test_archive_supports_safe_paths_containing_quotes(repository, tmp_path, fake_source):
    repository.save_snapshot(fake_source.snapshot_rows[:1])
    data_dir = tmp_path / "含'引号"

    output = archive_snapshots(repository, date(2026, 8, 4), data_dir)

    assert output.exists()
    assert repository.parquet_count(output) == 1
