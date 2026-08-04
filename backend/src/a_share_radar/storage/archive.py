from datetime import date
from pathlib import Path

from a_share_radar.storage.repository import MarketRepository


def archive_snapshots(
    repository: MarketRepository, trade_date: date, data_dir: Path
) -> Path:
    target_dir = data_dir / "snapshots" / f"trade_date={trade_date.isoformat()}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "part-000.parquet"
    temporary = target.with_suffix(".parquet.tmp")

    repository.copy_snapshots_to_parquet(trade_date, temporary)
    expected = repository.snapshot_count_for_date(trade_date)
    actual = repository.parquet_count(temporary)
    if expected != actual or actual == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"归档校验失败：热数据 {expected} 条，文件 {actual} 条")

    temporary.replace(target)
    repository.delete_snapshots_for_date(trade_date)
    return target
