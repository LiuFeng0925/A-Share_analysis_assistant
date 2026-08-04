import asyncio
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol


class Snapshot(Protocol):
    code: str
    captured_at: datetime


class SnapshotSource(Protocol):
    async def fetch_market_snapshot(self) -> Sequence[Snapshot]: ...


async def run(source: SnapshotSource | None = None) -> None:
    if os.environ.get("A_SHARE_RUN_LIVE") != "1":
        raise SystemExit("未访问公网：请显式设置 A_SHARE_RUN_LIVE=1 后再运行在线烟雾测试")

    if source is None:
        backend_src = Path(__file__).resolve().parents[1] / "backend" / "src"
        sys.path.insert(0, str(backend_src))
        from a_share_radar.data_sources.akshare_source import AkshareSource

        resolved_source: SnapshotSource = AkshareSource()
    else:
        resolved_source = source
    snapshots = list(await resolved_source.fetch_market_snapshot())
    if len(snapshots) <= 4000:
        raise RuntimeError(f"全市场行情数量异常：仅返回 {len(snapshots)} 条")

    codes = [snapshot.code for snapshot in snapshots]
    if len(codes) != len(set(codes)):
        raise RuntimeError("全市场行情包含重复股票代码")
    if "600519" not in codes:
        raise RuntimeError("全市场行情缺少贵州茅台 600519")

    captured_times = [
        snapshot.captured_at
        for snapshot in snapshots
        if isinstance(snapshot.captured_at, datetime)
    ]
    captured_at = max(captured_times) if captured_times else None
    captured_text = (
        captured_at.strftime("%Y-%m-%d %H:%M:%S") if captured_at else "上游未提供"
    )
    print(f"全市场股票数量：{len(snapshots)}")
    print(f"获取时间：{captured_text}")
    print("在线行情接口可用")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"在线行情接口检查失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
