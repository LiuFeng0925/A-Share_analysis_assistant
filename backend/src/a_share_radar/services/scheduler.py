import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.snapshot_collector import SnapshotCollector

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def create_scheduler(
    clock: MarketClock,
    collector: SnapshotCollector,
    archive_callback: Callable[[], Awaitable[None]],
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=SHANGHAI)

    async def collect_if_open() -> None:
        now = datetime.now(SHANGHAI)
        if not clock.is_open(now):
            return
        try:
            await collector.collect_once(now)
        except Exception:
            logger.exception("全市场行情采集失败，继续保留上一批有效数据")

    scheduler.add_job(
        collect_if_open,
        CronTrigger(minute="*", second=5, timezone=SHANGHAI),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        archive_callback,
        CronTrigger(hour=15, minute=10, day_of_week="mon-fri", timezone=SHANGHAI),
        max_instances=1,
        coalesce=True,
    )
    return scheduler
