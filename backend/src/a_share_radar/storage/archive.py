from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

from a_share_radar.storage.repository import MarketRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")


def archive_snapshots(
    repository: MarketRepository, trade_date: date, data_dir: Path
) -> Path:
    target_dir = data_dir / "snapshots" / f"trade_date={trade_date.isoformat()}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "part-000.parquet"

    with repository.database.lock:
        hot_count = repository.snapshot_count_for_date(trade_date)
        if hot_count == 0:
            if target.exists():
                return target
            raise RuntimeError("归档校验失败：热数据 0 条，文件 0 条")

        with NamedTemporaryFile(
            dir=target_dir,
            prefix="part-000-",
            suffix=".parquet.tmp",
            delete=False,
        ) as temporary_file:
            temporary = Path(temporary_file.name)

        try:
            repository.copy_snapshots_to_parquet(trade_date, temporary)
            expected = repository.merge_snapshot_parquet(target, temporary)
            actual = repository.parquet_count(temporary)
            if expected != actual or actual == 0:
                raise RuntimeError(
                    f"归档校验失败：合并数据 {expected} 条，文件 {actual} 条"
                )

            temporary.replace(target)
            repository.delete_snapshots_for_date(trade_date)
            return target
        finally:
            temporary.unlink(missing_ok=True)


def archive_pending_snapshots(
    repository: MarketRepository,
    data_dir: Path,
    at: datetime,
) -> dict[date, str]:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("归档时间必须包含时区信息")
    shanghai_at = at.astimezone(SHANGHAI)
    through_date = (
        shanghai_at.date()
        if shanghai_at.time() >= time(15, 10)
        else shanghai_at.date() - timedelta(days=1)
    )
    failures: dict[date, str] = {}
    for trade_date in repository.pending_snapshot_dates(through_date):
        try:
            archive_snapshots(repository, trade_date, data_dir)
        except Exception as exc:  # noqa: BLE001 - 单日失败不能阻断其他日期补归档
            failures[trade_date] = str(exc)
    return failures
