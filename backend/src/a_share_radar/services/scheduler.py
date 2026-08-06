import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.snapshot_collector import SnapshotCollector

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class _SchedulerRuntime:
    shutdown_complete: asyncio.Event
    jobs_idle: asyncio.Event
    active_jobs: int = 0


_RUNTIMES: WeakKeyDictionary[AsyncIOScheduler, _SchedulerRuntime] = WeakKeyDictionary()


def create_scheduler(
    clock: MarketClock,
    collector: SnapshotCollector,
    archive_callback: Callable[[], Awaitable[None]],
    maintenance_callback: Callable[[], Awaitable[None]] | None = None,
    daily_history_callback: Callable[[], Awaitable[None]] | None = None,
    indicator_callback: Callable[[datetime], Awaitable[None]] | None = None,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=SHANGHAI)
    jobs_idle = asyncio.Event()
    jobs_idle.set()
    runtime = _SchedulerRuntime(asyncio.Event(), jobs_idle)
    _RUNTIMES[scheduler] = runtime

    def mark_shutdown_complete(event) -> None:
        runtime.shutdown_complete.set()

    scheduler.add_listener(mark_shutdown_complete, EVENT_SCHEDULER_SHUTDOWN)

    async def run_managed(operation: Callable[[], Awaitable[None]]) -> None:
        runtime.active_jobs += 1
        runtime.jobs_idle.clear()
        try:
            await operation()
        finally:
            runtime.active_jobs -= 1
            if runtime.active_jobs == 0:
                runtime.jobs_idle.set()

    async def collect_if_open() -> None:
        async def collect() -> None:
            now = datetime.now(SHANGHAI)
            if not clock.is_open(now):
                return
            try:
                await collector.collect_once(now)
                if indicator_callback is not None:
                    try:
                        await indicator_callback(now)
                    except Exception:
                        logger.exception("MACD 指标刷新失败，继续保留上一批有效指标")
            except Exception:
                logger.exception("全市场行情采集失败，继续保留上一批有效数据")

        await run_managed(collect)

    async def archive_managed() -> None:
        await run_managed(archive_callback)

    async def maintenance_managed() -> None:
        if maintenance_callback is not None:
            await run_managed(maintenance_callback)

    async def daily_history_managed() -> None:
        if daily_history_callback is not None:
            await run_managed(daily_history_callback)

    scheduler.add_job(
        collect_if_open,
        CronTrigger(minute="*", second=5, timezone=SHANGHAI),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        archive_managed,
        CronTrigger(hour=15, minute=10, day_of_week="mon-fri", timezone=SHANGHAI),
        max_instances=1,
        coalesce=True,
    )
    if maintenance_callback is not None:
        scheduler.add_job(
            maintenance_managed,
            CronTrigger(minute="*/30", second=20, timezone=SHANGHAI),
            max_instances=1,
            coalesce=True,
        )
    if daily_history_callback is not None:
        scheduler.add_job(
            daily_history_managed,
            CronTrigger(hour=15, minute=20, day_of_week="mon-fri", timezone=SHANGHAI),
            max_instances=1,
            coalesce=True,
        )
    return scheduler


async def shutdown_scheduler(scheduler: AsyncIOScheduler) -> None:
    if not scheduler.running:
        return
    runtime = _RUNTIMES[scheduler]
    scheduler.pause()
    scheduler.shutdown(wait=False)
    await runtime.shutdown_complete.wait()
    await runtime.jobs_idle.wait()
