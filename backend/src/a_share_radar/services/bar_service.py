import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.models import Bar, Market, QualityStatus, Stock
from a_share_radar.storage.repository import MarketRepository

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")

RANGE_DAYS = {
    "5d": 7,
    "60d": 90,
    "6mo": 190,
    "ytd": 370,
    "1y": 370,
    "5y": 1830,
}

PERIODS = {"1m", "5m", "15m", "30m", "60m", "1d", "1w", "1mo"}
ADJUSTMENTS = {"none", "qfq", "hfq"}
_EARLIEST_BAR_TIME = datetime(1990, 1, 1, tzinfo=SHANGHAI)


class BarQueryValidationError(ValueError):
    """K 线查询参数不合法。"""


async def _run_repository_call[Result](function: Callable[..., Result], *args: object) -> Result:
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await asyncio.wait({worker})
        if error := worker.exception():
            logger.error("取消期间仓储线程执行失败", exc_info=error)
        raise


@dataclass(frozen=True, slots=True)
class _RecentResult:
    expires_at: float
    bars: tuple[Bar, ...]


QueryKey = tuple[Market, str, str, str, str]
DailyKey = tuple[Market, str]


class BarService:
    def __init__(
        self,
        source: MarketDataSource,
        repository: MarketRepository,
        history_days: int,
        query_ttl_seconds: float = 2.0,
    ) -> None:
        self.source = source
        self.repository = repository
        self.history_days = history_days
        self.query_ttl_seconds = query_ttl_seconds
        self._inflight: dict[QueryKey, asyncio.Task[list[Bar]]] = {}
        self._daily_inflight: dict[DailyKey, asyncio.Task[list[Bar]]] = {}
        self._recent: dict[QueryKey, _RecentResult] = {}
        self._closed = False

    @property
    def inflight_count(self) -> int:
        return len(self._inflight) + len(self._daily_inflight)

    async def ensure_daily_history(self, stock: Stock, end_date: date) -> list[Bar]:
        if self._closed:
            raise RuntimeError("K 线服务已关闭")
        key = (stock.market, stock.code)
        task = self._daily_inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._ensure_daily_history(stock, end_date))
            self._daily_inflight[key] = task
            task.add_done_callback(
                lambda completed, key=key: self._discard_daily_task(key, completed)
            )
        return list(await asyncio.shield(task))

    def _discard_daily_task(self, key: DailyKey, completed: asyncio.Task[list[Bar]]) -> None:
        if self._daily_inflight.get(key) is completed:
            del self._daily_inflight[key]

    async def _ensure_daily_history(self, stock: Stock, end_date: date) -> list[Bar]:
        end = datetime.combine(end_date, time(23, 59, 59), tzinfo=SHANGHAI)
        existing = await _run_repository_call(
            self.repository.get_bars,
            stock.market,
            stock.code,
            "1d",
            _EARLIEST_BAR_TIME,
            end,
            "qfq",
        )
        complete_existing = [bar for bar in existing if bar.is_complete]
        latest = complete_existing[-1] if complete_existing else None
        if latest is not None and latest.bar_time.date() >= end_date:
            return existing

        start_date = (
            latest.bar_time.date() + timedelta(days=1)
            if latest is not None
            else end_date - timedelta(days=max(100, self.history_days * 2))
        )
        fetched = await self.source.fetch_daily_bars(stock.code, start_date, end_date, "1d", "qfq")
        eligible = [bar for bar in fetched if bar.bar_time.date() <= end_date and bar.is_complete]
        selected = eligible if latest is not None else eligible[-self.history_days :]
        await _run_repository_call(self.repository.upsert_bars, selected)
        return await _run_repository_call(
            self.repository.get_bars,
            stock.market,
            stock.code,
            "1d",
            _EARLIEST_BAR_TIME,
            end,
            "qfq",
        )

    async def get_bars(
        self,
        market: Market,
        code: str,
        period: str,
        range_name: str,
        adjustment: str,
        now: datetime,
    ) -> list[Bar]:
        self._validate(period, range_name, adjustment)
        if self._closed:
            raise RuntimeError("K 线服务已关闭")
        key = (market, code, period, range_name, adjustment)
        recent = self._recent.get(key)
        current_monotonic = monotonic()
        if recent is not None and recent.expires_at >= current_monotonic:
            return list(recent.bars)
        if recent is not None:
            del self._recent[key]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(
                self._query_and_cache(key, market, code, period, range_name, adjustment, now)
            )
            self._inflight[key] = task
            task.add_done_callback(
                lambda completed, key=key: self._discard_query_task(key, completed)
            )
        return list(await asyncio.shield(task))

    def _discard_query_task(self, key: QueryKey, completed: asyncio.Task[list[Bar]]) -> None:
        if self._inflight.get(key) is completed:
            del self._inflight[key]

    async def _query_and_cache(
        self,
        key: QueryKey,
        market: Market,
        code: str,
        period: str,
        range_name: str,
        adjustment: str,
        now: datetime,
    ) -> list[Bar]:
        bars = await self._query_bars(market, code, period, range_name, adjustment, now)
        self._recent[key] = _RecentResult(monotonic() + self.query_ttl_seconds, tuple(bars))
        return bars

    async def _query_bars(
        self,
        market: Market,
        code: str,
        period: str,
        range_name: str,
        adjustment: str,
        now: datetime,
    ) -> list[Bar]:
        start, end = self._range(now, range_name)
        storage_end = (
            end
            if period.endswith("m")
            else datetime.combine(end.date(), time(23, 59, 59), tzinfo=SHANGHAI)
        )
        cached = await _run_repository_call(
            self.repository.get_bars,
            market,
            code,
            period,
            start,
            storage_end,
            adjustment,
        )
        if period.endswith("m"):
            fetched = await self._fetch_minute_increment(
                code, period, adjustment, start, end, cached
            )
        else:
            try:
                fetched = await self._fetch_history_increment(
                    code, period, adjustment, start, end, cached, now
                )
            except Exception:
                if not cached:
                    raise
                logger.exception("历史 K 线增量抓取失败，返回本地缓存")
                fetched = []
        normalized = self._mark_completion(fetched, now)
        await _run_repository_call(self.repository.upsert_bars, normalized)
        return await _run_repository_call(
            self.repository.get_bars,
            market,
            code,
            period,
            start,
            storage_end,
            adjustment,
        )

    async def _fetch_minute_increment(
        self,
        code: str,
        period: str,
        adjustment: str,
        start: datetime,
        end: datetime,
        cached: list[Bar],
    ) -> list[Bar]:
        fetch_start = start
        if cached:
            overlap = timedelta(minutes=int(period.removesuffix("m")))
            fetch_start = max(start, cached[-1].bar_time - overlap)
        try:
            return await self.source.fetch_minute_bars(code, fetch_start, end, period, adjustment)
        except Exception:
            if cached:
                logger.exception("分钟 K 线增量抓取失败，返回本地缓存")
                return []
            raise

    async def _fetch_history_increment(
        self,
        code: str,
        period: str,
        adjustment: str,
        start: datetime,
        end: datetime,
        cached: list[Bar],
        now: datetime,
    ) -> list[Bar]:
        fetch_start = start.date()
        if cached:
            tail = cached[-1]
            if period == "1d":
                if tail.bar_time.date() < end.date():
                    fetch_start = tail.bar_time.date() + timedelta(days=1)
                elif not tail.is_complete:
                    fetch_start = tail.bar_time.date()
                else:
                    return []
            else:
                acquired_at = tail.acquired_at or tail.bar_time
                if acquired_at >= now - timedelta(seconds=self.query_ttl_seconds):
                    return []
                overlap_days = 7 if period == "1w" else 31
                fetch_start = max(start.date(), tail.bar_time.date() - timedelta(days=overlap_days))
        return await self.source.fetch_daily_bars(code, fetch_start, end.date(), period, adjustment)

    @staticmethod
    def _mark_completion(bars: list[Bar], now: datetime) -> list[Bar]:
        shanghai_now = now.astimezone(SHANGHAI)
        normalized: list[Bar] = []
        for bar in bars:
            if bar.period.endswith("m"):
                duration = timedelta(minutes=int(bar.period.removesuffix("m")))
                complete = bar.bar_time + duration <= shanghai_now
            elif bar.period == "1d":
                complete = bar.bar_time.date() < shanghai_now.date() or (
                    bar.bar_time.date() == shanghai_now.date()
                    and shanghai_now.time() >= time(15, 0)
                )
            elif bar.period == "1w":
                complete = bar.bar_time.isocalendar()[:2] < shanghai_now.isocalendar()[:2]
            else:
                complete = (bar.bar_time.year, bar.bar_time.month) < (
                    shanghai_now.year,
                    shanghai_now.month,
                )
            normalized.append(
                replace(
                    bar,
                    is_complete=complete,
                    quality_status=(
                        bar.quality_status if complete else QualityStatus.PARTIAL
                    ),
                )
            )
        return normalized

    async def close(self) -> None:
        self._closed = True
        tasks = [*self._inflight.values(), *self._daily_inflight.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._recent.clear()

    @staticmethod
    def _validate(period: str, range_name: str, adjustment: str) -> None:
        if period not in PERIODS:
            raise BarQueryValidationError(f"不支持的 K 线周期：{period}")
        if adjustment not in ADJUSTMENTS:
            raise BarQueryValidationError(f"不支持的复权方式：{adjustment}")
        if range_name not in {"all", "today", *RANGE_DAYS}:
            raise BarQueryValidationError(f"不支持的时间范围：{range_name}")
        if range_name == "today" and period != "1m":
            raise BarQueryValidationError("今日视图只允许一分钟 K")
        if period == "1m" and adjustment != "none":
            raise BarQueryValidationError("免费一分钟 K 只允许不复权")

    @staticmethod
    def _range(now: datetime, range_name: str) -> tuple[datetime, datetime]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise BarQueryValidationError("K 线查询时间必须包含时区")
        shanghai_now = now.astimezone(SHANGHAI)
        if range_name == "today":
            return (
                datetime.combine(shanghai_now.date(), time(9, 30), tzinfo=SHANGHAI),
                shanghai_now,
            )
        if range_name == "ytd":
            return datetime(shanghai_now.year, 1, 1, tzinfo=SHANGHAI), shanghai_now
        if range_name == "all":
            return _EARLIEST_BAR_TIME, shanghai_now
        return shanghai_now - timedelta(days=RANGE_DAYS[range_name]), shanghai_now


class HistoryBootstrapper:
    def __init__(
        self,
        bar_service: BarService,
        repository: MarketRepository,
        delay_seconds: float,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.bar_service = bar_service
        self.repository = repository
        self.delay_seconds = delay_seconds
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI))
        self._run_lock = asyncio.Lock()
        self._rerun_requested = False

    async def run(self) -> None:
        if self._run_lock.locked():
            self._rerun_requested = True
            return
        async with self._run_lock:
            while True:
                self._rerun_requested = False
                await self._run_once()
                if not self._rerun_requested:
                    break

    async def _run_once(self) -> None:
        now = self.now_provider().astimezone(SHANGHAI)
        trading_days = await _run_repository_call(self.repository.list_trading_days)
        end_date = self._latest_completed_day(now, trading_days)
        stocks = await _run_repository_call(self.repository.list_all_stocks)
        for stock in stocks:
            try:
                await self.bar_service.ensure_daily_history(stock, end_date)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("日 K 回补失败：%s.%s", stock.market, stock.code)
            await asyncio.sleep(self.delay_seconds)

    @staticmethod
    def _latest_completed_day(now: datetime, trading_days: set[date]) -> date:
        cutoff_today = now.time() >= time(15, 10)
        candidates = [
            day for day in trading_days if day < now.date() or (cutoff_today and day == now.date())
        ]
        if candidates:
            return max(candidates)
        candidate = now.date() if cutoff_today else now.date() - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate
