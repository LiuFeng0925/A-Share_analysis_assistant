import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.domain.models import Stock
from a_share_radar.services.macd import calculate_macd_series
from a_share_radar.storage.repository import MarketRepository, StockQuoteRow

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
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
    def __init__(self, repository: MarketRepository):
        self.repository = repository

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
        if isinstance(stock, StockQuoteRow):
            return stock
        return await _run_repository_call(
            self.repository.get_stock, stock.market, stock.code
        )
