from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock

SHANGHAI = ZoneInfo("Asia/Shanghai")


class FixtureSource:
    """为本地演示和端到端测试提供完全离线、可重复的行情。"""

    name = "fixture"
    trade_date = date(2026, 8, 4)
    captured_at = datetime(2026, 8, 4, 10, 30, tzinfo=SHANGHAI)

    _stocks = (
        Stock("600519", Market.SH, "贵州茅台", list_date=date(2001, 8, 27)),
        Stock("000001", Market.SZ, "平安银行", list_date=date(1991, 4, 3)),
    )

    async def fetch_stock_master(self) -> list[Stock]:
        return list(self._stocks)

    async def fetch_trading_days(self, start: date, end: date) -> set[date]:
        return {day for day in self._trading_days() if start <= day <= end}

    async def fetch_market_snapshot(self) -> list[QuoteSnapshot]:
        return [
            QuoteSnapshot(
                code="600519",
                market=Market.SH,
                name="贵州茅台",
                captured_at=self.captured_at,
                latest_price=1588.88,
                change_percent=2.36,
                change_amount=36.56,
                open_price=1558.20,
                high_price=1599.90,
                low_price=1551.01,
                previous_close=1552.32,
                volume=3_821_100,
                amount=6_058_000_000.0,
                turnover_rate=0.30,
                total_market_cap=1_995_000_000_000.0,
                source=self.name,
                quality_status=QualityStatus.OK,
            ),
            QuoteSnapshot(
                code="000001",
                market=Market.SZ,
                name="平安银行",
                captured_at=self.captured_at,
                latest_price=11.28,
                change_percent=-0.70,
                change_amount=-0.08,
                open_price=11.36,
                high_price=11.39,
                low_price=11.25,
                previous_close=11.36,
                volume=45_312_000,
                amount=512_000_000.0,
                turnover_rate=0.23,
                total_market_cap=218_900_000_000.0,
                source=self.name,
                quality_status=QualityStatus.OK,
            ),
        ]

    async def fetch_daily_bars(
        self,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        if code not in {stock.code for stock in self._stocks}:
            return []
        if period != "1d" or adjustment != "qfq":
            return []
        return [
            bar
            for bar in self._daily_bars(code)
            if start <= bar.bar_time.date() <= end
        ]

    async def fetch_minute_bars(
        self,
        code: str,
        start: datetime,
        end: datetime,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        if code not in {stock.code for stock in self._stocks}:
            return []
        if period != "1m" or adjustment != "none":
            return []
        return [bar for bar in self._minute_bars(code) if start <= bar.bar_time <= end]

    @classmethod
    def _trading_days(cls) -> list[date]:
        days: list[date] = []
        cursor = cls.trade_date
        while len(days) < 60:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor -= timedelta(days=1)
        return list(reversed(days))

    @classmethod
    def _daily_bars(cls, code: str) -> list[Bar]:
        market = Market.SH if code == "600519" else Market.SZ
        base_price = 1500.0 if code == "600519" else 10.6
        step = 1.35 if code == "600519" else 0.012
        volume_base = 2_500_000 if code == "600519" else 35_000_000
        result: list[Bar] = []
        for index, trading_day in enumerate(cls._trading_days()):
            open_price = base_price + index * step
            close_price = open_price + (4.2 if index % 2 == 0 else -2.1) * (
                1 if code == "600519" else 0.01
            )
            high_price = max(open_price, close_price) + (6.0 if code == "600519" else 0.06)
            low_price = min(open_price, close_price) - (5.0 if code == "600519" else 0.05)
            volume = volume_base + index * (13_000 if code == "600519" else 180_000)
            result.append(
                Bar(
                    code=code,
                    market=market,
                    period="1d",
                    adjustment="qfq",
                    bar_time=datetime.combine(trading_day, time(15, 0), tzinfo=SHANGHAI),
                    open_price=round(open_price, 2),
                    high_price=round(high_price, 2),
                    low_price=round(low_price, 2),
                    close_price=round(close_price, 2),
                    volume=volume,
                    amount=round(volume * close_price, 2),
                    source=cls.name,
                    is_complete=True,
                )
            )
        return result

    @classmethod
    def _minute_bars(cls, code: str) -> list[Bar]:
        market = Market.SH if code == "600519" else Market.SZ
        base_price = 1578.0 if code == "600519" else 11.18
        price_step = 0.18 if code == "600519" else 0.0015
        volume_base = 18_000 if code == "600519" else 210_000
        result: list[Bar] = []
        for index in range(61):
            bar_time = datetime.combine(cls.trade_date, time(9, 30), tzinfo=SHANGHAI)
            bar_time += timedelta(minutes=index)
            open_price = base_price + index * price_step
            close_price = open_price + (0.32 if index % 2 == 0 else -0.16) * (
                1 if code == "600519" else 0.01
            )
            high_price = max(open_price, close_price) + (0.45 if code == "600519" else 0.008)
            low_price = min(open_price, close_price) - (0.38 if code == "600519" else 0.006)
            volume = volume_base + index * (110 if code == "600519" else 900)
            result.append(
                Bar(
                    code=code,
                    market=market,
                    period="1m",
                    adjustment="none",
                    bar_time=bar_time,
                    open_price=round(open_price, 3),
                    high_price=round(high_price, 3),
                    low_price=round(low_price, 3),
                    close_price=round(close_price, 3),
                    volume=volume,
                    amount=round(volume * close_price, 2),
                    source=cls.name,
                    is_complete=index < 60,
                )
            )
        return result
