from datetime import date, datetime
from typing import Protocol

from a_share_radar.domain.models import Bar, QuoteSnapshot, Stock


class MarketDataSource(Protocol):
    async def fetch_stock_master(self) -> list[Stock]: ...

    async def fetch_trading_days(self, start: date, end: date) -> set[date]: ...

    async def fetch_market_snapshot(self) -> list[QuoteSnapshot]: ...

    async def fetch_daily_bars(
        self, code: str, start: date, end: date, period: str, adjustment: str
    ) -> list[Bar]: ...

    async def fetch_minute_bars(
        self,
        code: str,
        start: datetime,
        end: datetime,
        period: str,
        adjustment: str,
    ) -> list[Bar]: ...
