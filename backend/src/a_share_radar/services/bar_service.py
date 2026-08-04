import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.models import Bar, BarFetchBatch, Market, QualityStatus, Stock
from a_share_radar.storage.repository import BarIngestionAudit, MarketRepository

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
_PROVIDER_LOCK_STRIPES = 256


class BarQueryValidationError(ValueError):
    """K 线查询参数不合法。"""


async def _run_repository_call[Result](
    function: Callable[..., Result], *args: object, **kwargs: object
) -> Result:
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
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
RangeValue = date | datetime
ProviderSeriesKey = tuple[str, Market, str, str, str]
ProviderOperation = Callable[
    [RangeValue, RangeValue], Awaitable[list[Bar] | BarFetchBatch]
]


class BarService:
    def __init__(
        self,
        source: MarketDataSource,
        repository: MarketRepository,
        history_days: int,
        query_ttl_seconds: float = 2.0,
        query_cache_max_entries: int = 256,
        range_recheck_seconds: float = 7 * 24 * 60 * 60,
    ) -> None:
        self.source = source
        self.repository = repository
        self.history_days = history_days
        self.query_ttl_seconds = query_ttl_seconds
        if query_cache_max_entries < 1:
            raise ValueError("K 线查询缓存上限必须大于零")
        if range_recheck_seconds <= 0:
            raise ValueError("K 线范围复查间隔必须大于零")
        self.query_cache_max_entries = query_cache_max_entries
        self.range_recheck_seconds = range_recheck_seconds
        self._inflight: dict[QueryKey, asyncio.Task[list[Bar]]] = {}
        self._daily_inflight: dict[DailyKey, asyncio.Task[list[Bar]]] = {}
        self._provider_inflight: set[asyncio.Task[list[Bar]]] = set()
        self._provider_locks = tuple(
            asyncio.Lock() for _ in range(_PROVIDER_LOCK_STRIPES)
        )
        self._recent: OrderedDict[QueryKey, _RecentResult] = OrderedDict()
        self._closed = False

    @property
    def inflight_count(self) -> int:
        return len(self._inflight) + len(self._daily_inflight)

    @property
    def recent_cache_size(self) -> int:
        self._prune_recent(monotonic())
        return len(self._recent)

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
        if not completed.cancelled():
            completed.exception()

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
        trading_days = await _run_repository_call(self.repository.list_trading_days)
        completed_days = sorted(day for day in trading_days if day <= end_date)
        if len(completed_days) < self.history_days:
            raise RuntimeError(
                f"交易日历不足 {self.history_days} 日，"
                f"当前仅 {len(completed_days)} 日，本轮不写入部分历史"
            )
        target_days = {
            day
            for day in completed_days[-self.history_days :]
            if stock.list_date is None or day >= stock.list_date
        }
        existing_days = {
            bar.bar_time.date()
            for bar in complete_existing
            if bar.bar_time.date() in target_days
        }
        missing_days = target_days - existing_days
        if not missing_days:
            return existing
        start_date = min(missing_days)
        fetch_end_date = max(missing_days)
        await self._fetch_daily_provider(
            stock.market, stock.code, start_date, fetch_end_date, "1d", "qfq"
        )
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
        current_monotonic = monotonic()
        self._prune_recent(current_monotonic)
        recent = self._recent.get(key)
        if recent is not None:
            self._recent.move_to_end(key)
            return list(recent.bars)

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
        if not completed.cancelled():
            completed.exception()

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
        self._recent.move_to_end(key)
        self._prune_recent(monotonic())
        return bars

    def _prune_recent(self, at: float) -> None:
        expired = [
            key for key, result in self._recent.items() if result.expires_at < at
        ]
        for key in expired:
            self._recent.pop(key, None)
        while len(self._recent) > self.query_cache_max_entries:
            self._recent.popitem(last=False)

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
            await self._fetch_minute_increment(
                market, code, period, adjustment, start, end, cached
            )
        else:
            try:
                await self._fetch_history_increment(
                    market, code, period, adjustment, start, end, cached, now
                )
            except Exception:
                if not cached:
                    raise
                logger.exception("历史 K 线增量抓取失败，返回本地缓存")
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
        market: Market,
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
            return await self._fetch_minute_provider(
                market,
                code,
                fetch_start,
                end,
                period,
                adjustment,
            )
        except Exception:
            if cached:
                logger.exception("分钟 K 线增量抓取失败，返回本地缓存")
                return []
            raise

    async def _fetch_history_increment(
        self,
        market: Market,
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
        return await self._fetch_daily_provider(
            market, code, fetch_start, end.date(), period, adjustment
        )

    async def _fetch_daily_provider(
        self,
        market: Market,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        async def operation(
            requested_start: RangeValue, requested_end: RangeValue
        ) -> list[Bar] | BarFetchBatch:
            if isinstance(requested_start, datetime) or isinstance(
                requested_end, datetime
            ):
                raise TypeError("历史 K 线范围必须使用日期")
            return await self.source.fetch_daily_bars(
                code, requested_start, requested_end, period, adjustment
            )

        return await self._coordinated_provider_fetch(
            "history",
            market,
            code,
            period,
            adjustment,
            start,
            end,
            operation,
        )

    async def _fetch_minute_provider(
        self,
        market: Market,
        code: str,
        start: datetime,
        end: datetime,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        async def operation(
            requested_start: RangeValue, requested_end: RangeValue
        ) -> list[Bar] | BarFetchBatch:
            if not isinstance(requested_start, datetime) or not isinstance(
                requested_end, datetime
            ):
                raise TypeError("分钟 K 线范围必须使用时间")
            return await self.source.fetch_minute_bars(
                code, requested_start, requested_end, period, adjustment
            )

        return await self._coordinated_provider_fetch(
            "minute",
            market,
            code,
            period,
            adjustment,
            start,
            end,
            operation,
        )

    async def _coordinated_provider_fetch(
        self,
        kind: str,
        market: Market,
        code: str,
        period: str,
        adjustment: str,
        start: RangeValue,
        end: RangeValue,
        operation: ProviderOperation,
    ) -> list[Bar]:
        series_key: ProviderSeriesKey = (
            kind,
            market,
            code,
            period,
            adjustment,
        )
        lock = self._provider_locks[hash(series_key) % len(self._provider_locks)]
        task = asyncio.create_task(
            self._run_coordinated_provider_fetch(
                lock,
                kind,
                market,
                code,
                period,
                adjustment,
                start,
                end,
                operation,
            )
        )
        self._provider_inflight.add(task)
        task.add_done_callback(self._discard_provider_task)
        return list(await asyncio.shield(task))

    def _discard_provider_task(self, completed: asyncio.Task[list[Bar]]) -> None:
        self._provider_inflight.discard(completed)
        if not completed.cancelled():
            completed.exception()

    async def _run_coordinated_provider_fetch(
        self,
        lock: asyncio.Lock,
        kind: str,
        market: Market,
        code: str,
        period: str,
        adjustment: str,
        start: RangeValue,
        end: RangeValue,
        operation: ProviderOperation,
    ) -> list[Bar]:
        async with lock:
            range_start, range_end = self._storage_range(kind, start, end)
            checked_after = datetime.now(SHANGHAI) - timedelta(
                seconds=self.range_recheck_seconds
            )
            confirmed = await _run_repository_call(
                self.repository.list_confirmed_bar_ranges,
                market,
                code,
                period,
                adjustment,
                range_start,
                range_end,
                checked_after,
            )
            confirmed_values = self._restore_ranges(kind, confirmed)
            remaining = self._subtract_confirmed_ranges(
                start, end, confirmed_values, period
            )
            fetched: list[Bar] = []
            for requested_start, requested_end in remaining:
                fetched.extend(
                    await self._run_provider_fetch(
                        kind,
                        market,
                        code,
                        period,
                        adjustment,
                        requested_start,
                        requested_end,
                        operation,
                    )
                )
            return fetched

    @staticmethod
    def _storage_range(
        kind: str, start: RangeValue, end: RangeValue
    ) -> tuple[datetime, datetime]:
        if kind == "history":
            if isinstance(start, datetime) or isinstance(end, datetime):
                raise TypeError("历史 K 线范围必须使用日期")
            return (
                datetime.combine(start, time.min, tzinfo=SHANGHAI),
                datetime.combine(end, time.max, tzinfo=SHANGHAI),
            )
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise TypeError("分钟 K 线范围必须使用时间")
        return start.astimezone(SHANGHAI), end.astimezone(SHANGHAI)

    @staticmethod
    def _restore_ranges(
        kind: str, ranges: list[tuple[datetime, datetime]]
    ) -> list[tuple[RangeValue, RangeValue]]:
        if kind == "history":
            return [(start.date(), end.date()) for start, end in ranges]
        return ranges

    @classmethod
    def _subtract_confirmed_ranges(
        cls,
        start: RangeValue,
        end: RangeValue,
        confirmed: list[tuple[RangeValue, RangeValue]],
        period: str,
    ) -> list[tuple[RangeValue, RangeValue]]:
        remaining = [(start, end)]
        for confirmed_start, confirmed_end in sorted(confirmed):
            next_remaining: list[tuple[RangeValue, RangeValue]] = []
            for segment_start, segment_end in remaining:
                if confirmed_end < segment_start or confirmed_start > segment_end:
                    next_remaining.append((segment_start, segment_end))
                    continue
                if confirmed_start > segment_start:
                    before_end = cls._shift_range_boundary(
                        confirmed_start, period, -1
                    )
                    if before_end >= segment_start:
                        next_remaining.append((segment_start, before_end))
                if confirmed_end < segment_end:
                    after_start = cls._shift_range_boundary(
                        confirmed_end, period, 1
                    )
                    if after_start <= segment_end:
                        next_remaining.append((after_start, segment_end))
            remaining = next_remaining
        return remaining

    @staticmethod
    def _shift_range_boundary(
        value: RangeValue, period: str, direction: int
    ) -> RangeValue:
        if isinstance(value, datetime):
            return value + direction * timedelta(seconds=1)
        return value + direction * timedelta(days=1)

    async def _run_provider_fetch(
        self,
        kind: str,
        market: Market,
        code: str,
        period: str,
        adjustment: str,
        start: RangeValue,
        end: RangeValue,
        operation: ProviderOperation,
    ) -> list[Bar]:
        started_at = datetime.now(SHANGHAI)
        source_name = str(
            getattr(self.source, "name", self.source.__class__.__name__)
        )
        try:
            result = await operation(start, end)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            acquired_at = datetime.now(SHANGHAI)
            range_start, range_end = self._storage_range(kind, start, end)
            await _run_repository_call(
                self.repository.record_bar_ingestion,
                market=market,
                code=code,
                period=period,
                adjustment=adjustment,
                started_at=started_at,
                acquired_at=acquired_at,
                source=source_name,
                market_time=None,
                raw_row_count=0,
                valid_row_count=0,
                invalid_row_count=0,
                status="failed",
                quality_status=QualityStatus.ERROR.value,
                error_message=type(error).__name__,
                range_start=range_start,
                range_end=range_end,
            )
            raise

        batch = self._coerce_batch(result, source_name)
        requested_bars = self._bars_in_requested_range(
            kind, list(batch.bars), start, end
        )
        bars = self._mark_completion(requested_bars)
        quality_status = (
            QualityStatus.PARTIAL
            if any(bar.quality_status is not QualityStatus.OK for bar in bars)
            else batch.quality_status
        )
        await _run_repository_call(self.repository.upsert_bars, bars)
        market_time = max((bar.bar_time for bar in bars), default=None)
        range_start, range_end = self._storage_range(kind, start, end)
        await _run_repository_call(
            self.repository.record_bar_ingestion,
            market=market,
            code=code,
            period=period,
            adjustment=adjustment,
            started_at=started_at,
            acquired_at=batch.acquired_at,
            source=batch.source,
            market_time=market_time,
            raw_row_count=len(bars) + batch.invalid_row_count,
            valid_row_count=len(bars),
            invalid_row_count=batch.invalid_row_count,
            status="success",
            quality_status=quality_status.value,
            error_message=None,
            range_start=range_start,
            range_end=range_end,
        )
        return bars

    @staticmethod
    def _bars_in_requested_range(
        kind: str,
        bars: list[Bar],
        start: RangeValue,
        end: RangeValue,
    ) -> list[Bar]:
        if kind == "history":
            if isinstance(start, datetime) or isinstance(end, datetime):
                raise TypeError("历史 K 线范围必须使用日期")
            return [bar for bar in bars if start <= bar.bar_time.date() <= end]
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            raise TypeError("分钟 K 线范围必须使用时间")
        return [bar for bar in bars if start <= bar.bar_time <= end]

    @staticmethod
    def _coerce_batch(
        result: list[Bar] | BarFetchBatch, source_name: str
    ) -> BarFetchBatch:
        if isinstance(result, BarFetchBatch):
            return result
        acquired_at = max(
            (bar.acquired_at or bar.bar_time for bar in result),
            default=datetime.now(SHANGHAI),
        )
        quality_status = (
            QualityStatus.PARTIAL
            if any(bar.quality_status is not QualityStatus.OK for bar in result)
            else QualityStatus.OK
        )
        return BarFetchBatch(
            bars=tuple(result),
            acquired_at=acquired_at,
            source=source_name,
            quality_status=quality_status,
            raw_row_count=len(result),
            invalid_row_count=0,
        )

    async def latest_ingestion(
        self, market: Market, code: str, period: str, adjustment: str
    ) -> BarIngestionAudit | None:
        return await _run_repository_call(
            self.repository.latest_bar_ingestion,
            market,
            code,
            period,
            adjustment,
        )

    @staticmethod
    def _mark_completion(bars: list[Bar]) -> list[Bar]:
        normalized: list[Bar] = []
        for bar in bars:
            acquired_at = (bar.acquired_at or bar.bar_time).astimezone(SHANGHAI)
            if bar.period.endswith("m"):
                duration = timedelta(minutes=int(bar.period.removesuffix("m")))
                complete = bar.bar_time + duration <= acquired_at
            elif bar.period == "1d":
                complete = bar.bar_time.date() < acquired_at.date() or (
                    bar.bar_time.date() == acquired_at.date()
                    and acquired_at.time() >= time(15, 0)
                )
            elif bar.period == "1w":
                complete = bar.bar_time.isocalendar()[:2] < acquired_at.isocalendar()[:2]
            else:
                complete = (bar.bar_time.year, bar.bar_time.month) < (
                    acquired_at.year,
                    acquired_at.month,
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
        tasks = list(
            {
                *self._inflight.values(),
                *self._daily_inflight.values(),
                *self._provider_inflight,
            }
        )
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
