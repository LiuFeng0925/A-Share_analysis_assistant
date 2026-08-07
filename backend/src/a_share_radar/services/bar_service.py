import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.bar_completion import DAILY_FINAL_TIME, bar_is_complete
from a_share_radar.domain.models import Bar, BarFetchBatch, Market, QualityStatus, Stock
from a_share_radar.storage.repository import (
    BarIngestionAudit,
    MarketRepository,
    StockQuoteRow,
)

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


class BarStockNotFoundError(LookupError):
    """K 线查询指定的股票不在本地主数据中。"""


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
        now_provider: Callable[[], datetime] | None = None,
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
        self.now_provider = now_provider or (lambda: datetime.now(SHANGHAI))
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
        if now.tzinfo is None or now.utcoffset() is None:
            raise BarQueryValidationError("K 线查询时间必须包含时区")
        if len(code) != 6 or not code.isascii() or not code.isdigit():
            raise BarStockNotFoundError("未找到该股票")
        stock = await _run_repository_call(self.repository.get_stock, market, code)
        if stock is None:
            raise BarStockNotFoundError("未找到该股票")
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
            fetched: list[Bar] = []
            try:
                fetched = await self._fetch_history_increment(
                    market, code, period, adjustment, start, end, cached, now
                )
            except Exception:
                if not cached:
                    raise
                logger.exception("历史 K 线增量抓取失败，返回本地缓存")
            if period == "1d":
                await self._fill_daily_bar_from_latest_quote_if_lagged(
                    market, code, adjustment, start, end, cached, fetched, now
                )
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
            logger.exception("分钟 K 线增量抓取失败且本地无缓存，返回空结果")
            return []

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
            repair_start = self._earliest_daily_volume_repair_date(cached)
            if period == "1d" and repair_start is not None:
                return await self._fetch_daily_provider(
                    market,
                    code,
                    max(start.date(), repair_start),
                    end.date(),
                    period,
                    adjustment,
                    force=True,
                )
            tail = cached[-1]
            if period == "1d":
                if tail.quality_status is not QualityStatus.OK:
                    fetch_start = tail.bar_time.date()
                elif tail.bar_time.date() < end.date():
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

    @classmethod
    def _earliest_daily_volume_repair_date(cls, bars: list[Bar]) -> date | None:
        repair_dates = [
            bar.bar_time.date()
            for bar in bars
            if cls._daily_volume_unit_looks_like_lots(bar)
        ]
        return min(repair_dates, default=None)

    @staticmethod
    def _daily_volume_unit_looks_like_lots(bar: Bar) -> bool:
        if (
            bar.period != "1d"
            or "tencent" not in bar.source.lower()
            or bar.quality_status is not QualityStatus.OK
            or bar.volume <= 0
            or bar.amount <= 0
            or bar.close_price <= 0
        ):
            return False
        turnover_ratio = bar.amount / (bar.close_price * bar.volume)
        return 20 <= turnover_ratio <= 200

    async def _fill_daily_bar_from_latest_quote_if_lagged(
        self,
        market: Market,
        code: str,
        adjustment: str,
        start: datetime,
        end: datetime,
        cached: list[Bar],
        fetched: list[Bar],
        now: datetime,
    ) -> None:
        latest_quote = await _run_repository_call(self.repository.get_stock, market, code)
        supplemental = self._daily_bar_from_latest_quote(
            latest_quote, adjustment, start, end, cached, fetched, now
        )
        if supplemental is None:
            supplemental = await self._daily_bar_from_intraday_minutes_if_lagged(
                market, code, adjustment, start, end, cached, fetched, now, latest_quote
            )
        if supplemental is not None:
            await _run_repository_call(self.repository.upsert_bars, [supplemental])

    async def _daily_bar_from_intraday_minutes_if_lagged(
        self,
        market: Market,
        code: str,
        adjustment: str,
        start: datetime,
        end: datetime,
        cached: list[Bar],
        fetched: list[Bar],
        now: datetime,
        latest_quote: StockQuoteRow | None,
    ) -> Bar | None:
        quote_day = self._supplemental_quote_day(
            latest_quote, start, end, cached, fetched, now
        )
        if quote_day is None:
            return None
        minute_start = datetime.combine(quote_day, time(9, 30), tzinfo=SHANGHAI)
        minute_end = min(
            datetime.combine(quote_day, time(15, 0), tzinfo=SHANGHAI),
            end,
            now.astimezone(SHANGHAI),
        )
        if minute_end < minute_start:
            return None
        minute_bars = await _run_repository_call(
            self.repository.get_bars,
            market,
            code,
            "30m",
            minute_start,
            minute_end,
            "none",
        )
        try:
            await self._fetch_minute_provider(
                market, code, minute_start, minute_end, "30m", "none"
            )
            minute_bars = await _run_repository_call(
                self.repository.get_bars,
                market,
                code,
                "30m",
                minute_start,
                minute_end,
                "none",
            )
        except Exception:
            if not minute_bars:
                logger.exception("用 30 分钟 K 聚合今日线失败，且本地无分钟缓存")
                return None
            logger.exception("用 30 分钟 K 聚合今日线时增量抓取失败，使用本地分钟缓存")
        return self._daily_bar_from_minute_bars(
            market, code, adjustment, quote_day, minute_bars, now
        )

    @staticmethod
    def _supplemental_quote_day(
        latest_quote: StockQuoteRow | None,
        start: datetime,
        end: datetime,
        cached: list[Bar],
        fetched: list[Bar],
        now: datetime,
    ) -> date | None:
        if (
            latest_quote is None
            or latest_quote.latest_price is None
            or latest_quote.captured_at is None
        ):
            return None
        quote_time = latest_quote.captured_at.astimezone(SHANGHAI)
        quote_day = quote_time.date()
        if quote_day < start.date() or quote_day > end.date() or quote_day > now.date():
            return None
        if cached:
            tail = cached[-1]
            if tail.bar_time.date() > quote_day:
                return None
            if tail.bar_time.date() == quote_day and tail.quality_status is QualityStatus.OK:
                return None
        if fetched and max(bar.bar_time.date() for bar in fetched) >= quote_day:
            return None
        return quote_day

    @staticmethod
    def _daily_bar_from_minute_bars(
        market: Market,
        code: str,
        adjustment: str,
        day: date,
        minute_bars: list[Bar],
        now: datetime,
    ) -> Bar | None:
        day_bars = sorted(
            (bar for bar in minute_bars if bar.bar_time.astimezone(SHANGHAI).date() == day),
            key=lambda bar: bar.bar_time,
        )
        if not day_bars:
            return None
        bar_time = datetime.combine(day, time(15, 0), tzinfo=SHANGHAI)
        acquired_at = max((bar.acquired_at or bar.bar_time for bar in day_bars))
        is_complete = bar_is_complete("1d", bar_time, now.astimezone(SHANGHAI))
        return Bar(
            code=code,
            market=market,
            period="1d",
            adjustment=adjustment,
            bar_time=bar_time,
            open_price=day_bars[0].open_price,
            high_price=max(bar.high_price for bar in day_bars),
            low_price=min(bar.low_price for bar in day_bars),
            close_price=day_bars[-1].close_price,
            volume=sum(bar.volume for bar in day_bars),
            amount=sum(bar.amount for bar in day_bars),
            source="intraday-30m",
            is_complete=is_complete,
            acquired_at=acquired_at,
            quality_status=QualityStatus.PARTIAL,
        )

    @staticmethod
    def _daily_bar_from_latest_quote(
        latest_quote: StockQuoteRow | None,
        adjustment: str,
        start: datetime,
        end: datetime,
        cached: list[Bar],
        fetched: list[Bar],
        now: datetime,
    ) -> Bar | None:
        if (
            latest_quote is None
            or latest_quote.latest_price is None
            or latest_quote.open_price is None
            or latest_quote.high_price is None
            or latest_quote.low_price is None
            or latest_quote.captured_at is None
        ):
            return None
        quote_day = BarService._supplemental_quote_day(
            latest_quote, start, end, cached, fetched, now
        )
        if quote_day is None:
            return None

        quote_time = latest_quote.captured_at.astimezone(SHANGHAI)
        open_price = latest_quote.open_price
        high_price = latest_quote.high_price
        low_price = latest_quote.low_price
        close_price = latest_quote.latest_price
        is_complete = bar_is_complete(
            "1d",
            datetime.combine(quote_day, time(15, 0), tzinfo=SHANGHAI),
            quote_time,
        )
        return Bar(
            code=latest_quote.code,
            market=latest_quote.market,
            period="1d",
            adjustment=adjustment,
            bar_time=datetime.combine(quote_day, time(15, 0), tzinfo=SHANGHAI),
            open_price=open_price,
            high_price=max(high_price, open_price, close_price),
            low_price=min(low_price, open_price, close_price),
            close_price=close_price,
            volume=latest_quote.volume or 0,
            amount=latest_quote.amount or 0.0,
            source=latest_quote.source or "latest-quote",
            is_complete=is_complete,
            acquired_at=quote_time,
            quality_status=QualityStatus.PARTIAL,
        )

    async def _fetch_daily_provider(
        self,
        market: Market,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
        force: bool = False,
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
            force=force,
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
        force: bool = False,
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
                force,
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
        force: bool,
    ) -> list[Bar]:
        async with lock:
            range_start, range_end = self._storage_range(kind, start, end)
            if force:
                remaining = [(start, end)]
            else:
                confirmed = await _run_repository_call(
                    self.repository.list_confirmed_bar_ranges,
                    market,
                    code,
                    period,
                    adjustment,
                    range_start,
                    range_end,
                    self._now(),
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
        started_at = self._now()
        source_name = str(
            getattr(self.source, "name", self.source.__class__.__name__)
        )
        try:
            result = await operation(start, end)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            acquired_at = self._now()
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
                expires_at=acquired_at,
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
        expires_at = self._range_expires_at(
            kind,
            period,
            end,
            batch.acquired_at,
            quality_status,
        )
        confirmed_ranges = None
        if (
            kind == "history"
            and period == "1d"
            and bars
            and quality_status is QualityStatus.OK
        ):
            confirmed_ranges = tuple(
                self._storage_range(
                    "history", bar.bar_time.date(), bar.bar_time.date()
                )
                for bar in bars
            )
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
            expires_at=expires_at,
            confirmed_ranges=confirmed_ranges,
        )
        return bars

    def _range_expires_at(
        self,
        kind: str,
        period: str,
        end: RangeValue,
        acquired_at: datetime,
        quality_status: QualityStatus,
    ) -> datetime:
        acquired_at = self._as_shanghai_time(acquired_at)
        if quality_status is not QualityStatus.OK:
            return acquired_at
        ttl_seconds = (
            self.range_recheck_seconds
            if self._is_range_tail_closed(kind, period, end, acquired_at)
            else self.query_ttl_seconds
        )
        return acquired_at + timedelta(seconds=ttl_seconds)

    @staticmethod
    def _is_range_tail_closed(
        kind: str,
        period: str,
        end: RangeValue,
        acquired_at: datetime,
    ) -> bool:
        if kind == "minute":
            if not isinstance(end, datetime):
                raise TypeError("分钟 K 线范围必须使用时间")
            duration_minutes = int(period.removesuffix("m"))
            localized_end = BarService._as_shanghai_time(end)
            if localized_end.date() < acquired_at.date():
                return True
            if localized_end.date() > acquired_at.date():
                return False

            morning_start = datetime.combine(
                localized_end.date(), time(9, 30), tzinfo=SHANGHAI
            )
            morning_end = datetime.combine(
                localized_end.date(), time(11, 30), tzinfo=SHANGHAI
            )
            afternoon_start = datetime.combine(
                localized_end.date(), time(13, 0), tzinfo=SHANGHAI
            )
            afternoon_end = datetime.combine(
                localized_end.date(), time(15, 0), tzinfo=SHANGHAI
            )
            if localized_end < morning_start:
                return False
            if localized_end < morning_end:
                bucket_end = BarService._minute_bucket_end(
                    localized_end, morning_start, duration_minutes
                )
                return bucket_end <= acquired_at
            if localized_end < afternoon_start:
                return morning_end <= acquired_at
            if localized_end < afternoon_end:
                bucket_end = BarService._minute_bucket_end(
                    localized_end, afternoon_start, duration_minutes
                )
                return bucket_end <= acquired_at
            return afternoon_end <= acquired_at

        if isinstance(end, datetime):
            raise TypeError("历史 K 线范围必须使用日期")
        if period == "1d":
            return end < acquired_at.date() or (
                end == acquired_at.date() and acquired_at.time() >= time(15, 20)
            )
        if period == "1w":
            endpoint_week = end.isocalendar()[:2]
            acquired_week = acquired_at.isocalendar()[:2]
            return endpoint_week < acquired_week or (
                endpoint_week == acquired_week
                and (
                    acquired_at.weekday() > 4
                    or (
                        acquired_at.weekday() == 4
                        and acquired_at.time() >= time(15, 20)
                    )
                )
            )
        if period == "1mo":
            return (end.year, end.month) < (
                acquired_at.year,
                acquired_at.month,
            )
        raise ValueError(f"不支持的历史 K 线周期：{period}")

    @staticmethod
    def _minute_bucket_end(
        endpoint: datetime,
        session_start: datetime,
        duration_minutes: int,
    ) -> datetime:
        elapsed_seconds = (endpoint - session_start).total_seconds()
        bucket_index = int(elapsed_seconds // (duration_minutes * 60))
        return session_start + timedelta(
            minutes=(bucket_index + 1) * duration_minutes
        )

    def _now(self) -> datetime:
        return self._as_shanghai_time(self.now_provider())

    @staticmethod
    def _as_shanghai_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("K 线范围时间必须包含时区")
        return value.astimezone(SHANGHAI)

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
            complete = bar_is_complete(bar.period, bar.bar_time, acquired_at)
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
        cutoff_today = now.time() >= DAILY_FINAL_TIME
        candidates = [
            day for day in trading_days if day < now.date() or (cutoff_today and day == now.date())
        ]
        if candidates:
            return max(candidates)
        candidate = now.date() if cutoff_today else now.date() - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate
