import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.domain.indicators import MacdCalculation
from a_share_radar.domain.models import Stock
from a_share_radar.services.macd import calculate_macd_series
from a_share_radar.storage.repository import MarketRepository, StockQuoteRow

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
MACD_DIVERGENCE_ALGORITHM_VERSION = datetime(2026, 8, 7, 21, 50, tzinfo=SHANGHAI)
MACD_LOOKBACK_DAYS = 220
SUPPORTED_MACD_PERIODS = {"1m", "5m", "15m", "30m", "60m", "1d", "1w", "1mo"}
MACD_LOOKBACK_DAYS_BY_PERIOD = {
    "1m": 7,
    "5m": 90,
    "15m": 90,
    "30m": 90,
    "60m": 90,
    "1d": MACD_LOOKBACK_DAYS,
    "1w": 1830,
    "1mo": 3650,
}
MACD_BAR_RANGE_BY_PERIOD = {
    "1m": "today",
    "5m": "6mo",
    "15m": "6mo",
    "30m": "6mo",
    "60m": "6mo",
    "1d": "1y",
    "1w": "5y",
    "1mo": "all",
}


async def _run_repository_call[Result](
    function: Callable[..., Result], *args: object, **kwargs: object
) -> Result:
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await asyncio.wait({worker})
        if error := worker.exception():
            logger.error("取消期间指标仓储线程执行失败", exc_info=error)
        raise


class IndicatorService:
    def __init__(self, repository: MarketRepository, bar_service=None):
        self.repository = repository
        self.bar_service = bar_service

    async def get_stock_macd(
        self,
        stock: Stock | StockQuoteRow,
        now: datetime,
        *,
        market_open: bool,
        period: str = "1d",
    ) -> MacdCalculation | None:
        if period not in SUPPORTED_MACD_PERIODS:
            raise ValueError(f"不支持的 MACD 周期：{period}")
        await self._ensure_period_bars(stock, now, period)
        calculation = await _run_repository_call(
            self.repository.get_macd, stock.market, stock.code, period
        )
        if calculation is None or await self._macd_is_stale(
            calculation, stock, now, market_open=market_open
        ):
            await self.refresh_stock_macd(
                stock,
                now,
                market_open=market_open,
                period=period,
            )
            calculation = await _run_repository_call(
                self.repository.get_macd, stock.market, stock.code, period
            )
        return calculation

    async def refresh_stock_macd(
        self,
        stock: Stock | StockQuoteRow,
        now: datetime,
        *,
        market_open: bool,
        period: str = "1d",
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("MACD 刷新时间必须包含时区")
        if period not in SUPPORTED_MACD_PERIODS:
            raise ValueError(f"不支持的 MACD 周期：{period}")
        market = stock.market
        code = stock.code
        lookback_days = MACD_LOOKBACK_DAYS_BY_PERIOD[period]
        adjustment = "none" if period == "1m" else "qfq"
        latest_quote = await self._latest_quote(stock)
        bars = await _run_repository_call(
            self.repository.get_bars,
            market,
            code,
            period,
            datetime.combine(
                now.date() - timedelta(days=lookback_days),
                time.min,
                tzinfo=SHANGHAI,
            ),
            datetime.combine(now.date(), time(23, 59, 59), tzinfo=SHANGHAI),
            adjustment,
        )
        trading_days = await _run_repository_call(self.repository.list_trading_days)
        calculation = calculate_macd_series(
            bars,
            latest_quote=latest_quote,
            trading_days=sorted(trading_days),
            now=now,
            market_open=market_open,
            period=period,
        )
        await _run_repository_call(self.repository.upsert_macd, calculation)

    async def refresh_market_macd(self, now: datetime, *, market_open: bool) -> None:
        stocks = await _run_repository_call(self.repository.list_all_stocks)
        for stock in stocks:
            try:
                await self.refresh_stock_macd(
                    stock, now, market_open=market_open, period="1d"
                )
            except Exception:
                logger.exception("刷新 MACD 指标失败：%s.%s", stock.market.value, stock.code)

    async def _latest_quote(self, stock: Stock | StockQuoteRow) -> StockQuoteRow | None:
        latest = await _run_repository_call(
            self.repository.get_stock, stock.market, stock.code
        )
        if latest is not None:
            return latest
        if isinstance(stock, StockQuoteRow):
            return stock
        return None

    async def _ensure_period_bars(
        self,
        stock: Stock | StockQuoteRow,
        now: datetime,
        period: str,
    ) -> None:
        if self.bar_service is None:
            return
        adjustment = "none" if period == "1m" else "qfq"
        await self.bar_service.get_bars(
            stock.market,
            stock.code,
            period,
            MACD_BAR_RANGE_BY_PERIOD[period],
            adjustment,
            now,
        )

    async def _macd_is_stale(
        self,
        calculation: MacdCalculation,
        stock: Stock | StockQuoteRow,
        now: datetime,
        *,
        market_open: bool,
    ) -> bool:
        if calculation.summary.market_time is None:
            return True
        if calculation.summary.quality.value == "insufficient":
            return True

        period = calculation.summary.period
        if (
            period == "1d"
            and now >= MACD_DIVERGENCE_ALGORITHM_VERSION
            and calculation.summary.calculated_at < MACD_DIVERGENCE_ALGORITHM_VERSION
        ):
            return True
        lookback_days = MACD_LOOKBACK_DAYS_BY_PERIOD[period]
        adjustment = "none" if period == "1m" else "qfq"
        bars = await _run_repository_call(
            self.repository.get_bars,
            stock.market,
            stock.code,
            period,
            datetime.combine(
                now.date() - timedelta(days=lookback_days),
                time.min,
                tzinfo=SHANGHAI,
            ),
            datetime.combine(now.date(), time(23, 59, 59), tzinfo=SHANGHAI),
            adjustment,
        )
        if bars:
            latest_bar = bars[-1]
            if latest_bar.bar_time > calculation.summary.market_time:
                return True
            if (
                latest_bar.bar_time == calculation.summary.market_time
                and latest_bar.acquired_at > calculation.summary.calculated_at
            ):
                return True

        latest_quote = await self._latest_quote(stock)
        return bool(
            period == "1d"
            and market_open
            and latest_quote is not None
            and latest_quote.captured_at > calculation.summary.calculated_at
        )
