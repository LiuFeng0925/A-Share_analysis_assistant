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
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("MACD 刷新时间必须包含时区")
        market = stock.market
        code = stock.code
        latest_quote = await self._latest_quote(stock)
        bars = await _run_repository_call(
            self.repository.get_bars,
            market,
            code,
            "1d",
            datetime.combine(
                now.date() - timedelta(days=MACD_LOOKBACK_DAYS),
                time.min,
                tzinfo=SHANGHAI,
            ),
            datetime.combine(now.date(), time(23, 59, 59), tzinfo=SHANGHAI),
            "qfq",
        )
        trading_days = await _run_repository_call(self.repository.list_trading_days)
        calculation = calculate_macd_series(
            bars,
            latest_quote=latest_quote,
            trading_days=sorted(trading_days),
            now=now,
            market_open=market_open,
        )
        await _run_repository_call(self.repository.upsert_macd, calculation)

    async def refresh_market_macd(self, now: datetime, *, market_open: bool) -> None:
        stocks = await _run_repository_call(self.repository.list_all_stocks)
        for stock in stocks:
            try:
                await self.refresh_stock_macd(stock, now, market_open=market_open)
            except Exception:
                logger.exception("刷新 MACD 指标失败：%s.%s", stock.market.value, stock.code)

    async def _latest_quote(self, stock: Stock | StockQuoteRow) -> StockQuoteRow | None:
        if isinstance(stock, StockQuoteRow):
            return stock
        return await _run_repository_call(
            self.repository.get_stock, stock.market, stock.code
        )
