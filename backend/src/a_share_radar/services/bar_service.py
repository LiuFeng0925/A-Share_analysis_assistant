import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.models import Bar, Market, Stock
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


class BarService:
    def __init__(
        self,
        source: MarketDataSource,
        repository: MarketRepository,
        history_days: int,
    ) -> None:
        self.source = source
        self.repository = repository
        self.history_days = history_days
        self._locks: defaultdict[
            tuple[Market, str, str, str, str], asyncio.Lock
        ] = defaultdict(asyncio.Lock)

    async def ensure_daily_history(self, stock: Stock, end_date: date) -> list[Bar]:
        start_date = end_date - timedelta(days=100)
        fetched = await self.source.fetch_daily_bars(
            stock.code, start_date, end_date, "1d", "qfq"
        )
        selected = fetched[-self.history_days :]
        self.repository.upsert_bars(selected)
        return selected

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
        start, end = self._range(now, range_name)
        key = (market, code, period, range_name, adjustment)
        async with self._locks[key]:
            cached = self.repository.get_bars(
                market, code, period, start, end, adjustment
            )
            if period.endswith("m"):
                try:
                    fetched = await self.source.fetch_minute_bars(
                        code, start, end, period, adjustment
                    )
                except Exception:
                    if cached:
                        return cached
                    raise
            elif not cached:
                fetched = await self.source.fetch_daily_bars(
                    code, start.date(), end.date(), period, adjustment
                )
            else:
                fetched = []
            self.repository.upsert_bars(fetched)
            return self.repository.get_bars(
                market, code, period, start, end, adjustment
            )

    @staticmethod
    def _validate(period: str, range_name: str, adjustment: str) -> None:
        if period not in PERIODS:
            raise ValueError(f"不支持的 K 线周期：{period}")
        if adjustment not in ADJUSTMENTS:
            raise ValueError(f"不支持的复权方式：{adjustment}")
        if range_name not in {"all", "today", *RANGE_DAYS}:
            raise ValueError(f"不支持的时间范围：{range_name}")
        if range_name == "today" and period != "1m":
            raise ValueError("今日视图只允许一分钟 K")
        if period == "1m" and adjustment != "none":
            raise ValueError("免费一分钟 K 只允许不复权")

    @staticmethod
    def _range(now: datetime, range_name: str) -> tuple[datetime, datetime]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("K 线查询时间必须包含时区")
        shanghai_now = now.astimezone(SHANGHAI)
        if range_name == "today":
            return (
                datetime.combine(shanghai_now.date(), time(9, 30), tzinfo=SHANGHAI),
                shanghai_now,
            )
        if range_name == "ytd":
            return datetime(shanghai_now.year, 1, 1, tzinfo=SHANGHAI), shanghai_now
        if range_name == "all":
            return datetime(1990, 1, 1, tzinfo=SHANGHAI), shanghai_now
        return shanghai_now - timedelta(days=RANGE_DAYS[range_name]), shanghai_now


class HistoryBootstrapper:
    def __init__(
        self,
        bar_service: BarService,
        repository: MarketRepository,
        delay_seconds: float,
    ) -> None:
        self.bar_service = bar_service
        self.repository = repository
        self.delay_seconds = delay_seconds

    async def run(self) -> None:
        end_date = datetime.now(SHANGHAI).date()
        for stock in self.repository.list_all_stocks():
            try:
                await self.bar_service.ensure_daily_history(stock, end_date)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("日 K 回补失败：%s.%s", stock.market, stock.code)
            await asyncio.sleep(self.delay_seconds)
