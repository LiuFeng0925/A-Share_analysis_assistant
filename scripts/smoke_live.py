import asyncio
import math
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol


class Snapshot(Protocol):
    code: str
    market: object
    name: str
    captured_at: datetime
    latest_price: float
    change_percent: float
    change_amount: float
    open_price: float
    high_price: float
    low_price: float
    previous_close: float
    volume: int
    amount: float
    turnover_rate: float
    total_market_cap: float
    quality_status: object


class SnapshotSource(Protocol):
    async def fetch_market_snapshot(self) -> Sequence[Snapshot]: ...


SHANGHAI_OFFSET = timedelta(hours=8)
REPRESENTATIVE_CODE = "600519"
REQUIRED_FIELDS = (
    "market",
    "name",
    "captured_at",
    "latest_price",
    "change_percent",
    "change_amount",
    "open_price",
    "high_price",
    "low_price",
    "previous_close",
    "volume",
    "amount",
    "turnover_rate",
    "total_market_cap",
    "quality_status",
)


def _finite_number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"代表股票{label}字段不是有限数值")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise RuntimeError(f"代表股票{label}字段不是有限正数")
    return number


def _validate_representative(snapshot: Snapshot) -> None:
    missing = [field for field in REQUIRED_FIELDS if not hasattr(snapshot, field)]
    if missing:
        raise RuntimeError(f"代表股票缺少必需字段：{', '.join(missing)}")

    captured_at = snapshot.captured_at
    if (
        not isinstance(captured_at, datetime)
        or captured_at.tzinfo is None
        or captured_at.utcoffset() != SHANGHAI_OFFSET
    ):
        raise RuntimeError("代表股票采集时间必须包含上海时区")

    latest = _finite_number(snapshot.latest_price, "最新价", positive=True)
    open_price = _finite_number(snapshot.open_price, "OHLC 开盘", positive=True)
    high = _finite_number(snapshot.high_price, "OHLC 最高", positive=True)
    low = _finite_number(snapshot.low_price, "OHLC 最低", positive=True)
    previous_close = _finite_number(snapshot.previous_close, "昨收", positive=True)
    _finite_number(snapshot.change_percent, "涨跌幅")
    _finite_number(snapshot.change_amount, "涨跌额")
    _finite_number(snapshot.turnover_rate, "换手率")
    _finite_number(snapshot.total_market_cap, "总市值", positive=True)
    if low > min(open_price, latest) or high < max(open_price, latest) or low > high:
        raise RuntimeError("代表股票 OHLC 关系不合法")

    volume = snapshot.volume
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
        raise RuntimeError("代表股票成交量必须是非负整数")
    if volume % 100 != 0:
        raise RuntimeError("代表股票成交量未按股口径归一化")
    amount = _finite_number(snapshot.amount, "成交额")
    if amount < 0:
        raise RuntimeError("代表股票成交额不能为负数")
    if volume > 0:
        average_price = amount / volume
        if not low <= average_price <= high:
            raise RuntimeError("代表股票量额关系不符合成交量股口径")
    elif amount != 0:
        raise RuntimeError("代表股票零成交量时成交额必须为零")

    if str(snapshot.market) not in {"SH", "SZ", "BJ"}:
        raise RuntimeError("代表股票市场字段不合法")
    if not str(snapshot.name).strip():
        raise RuntimeError("代表股票名称为空")
    if str(snapshot.quality_status) not in {"ok", "partial", "stale", "error"}:
        raise RuntimeError("代表股票质量状态不合法")
    if previous_close <= 0:
        raise RuntimeError("代表股票昨收必须为正数")


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
    if REPRESENTATIVE_CODE not in codes:
        raise RuntimeError("全市场行情缺少贵州茅台 600519")

    representative = snapshots[codes.index(REPRESENTATIVE_CODE)]
    _validate_representative(representative)

    captured_times = [
        snapshot.captured_at
        for snapshot in snapshots
        if isinstance(snapshot.captured_at, datetime)
        and snapshot.captured_at.tzinfo is not None
        and snapshot.captured_at.utcoffset() is not None
    ]
    captured_at = max(captured_times) if captured_times else None
    captured_text = (
        captured_at.strftime("%Y-%m-%d %H:%M:%S") if captured_at else "上游未提供"
    )
    print(f"全市场股票数量：{len(snapshots)}")
    print(f"获取时间：{captured_text}")
    print(f"代表股票校验：{representative.code} {representative.name}")
    print("成交量口径：股；成交额口径：元")
    print("在线行情接口可用")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"在线行情接口检查失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
