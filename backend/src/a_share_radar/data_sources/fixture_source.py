from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import ClassVar
from zoneinfo import ZoneInfo

from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class FixtureStockProfile:
    stock: Stock
    latest_price: float
    change_percent: float
    change_amount: float
    open_price: float
    high_price: float
    low_price: float
    previous_close: float
    volume: int
    amount: float
    turnover_rate: float
    total_market_cap: float
    daily_base_price: float
    daily_step: float
    daily_volume_base: int
    minute_base_price: float
    minute_step: float
    minute_volume_base: int


class FixtureSource:
    """为本地演示和端到端测试提供完全离线、可重复的行情。"""

    name = "fixture"
    trade_date = date(2026, 8, 4)
    captured_at = datetime(2026, 8, 4, 10, 30, tzinfo=SHANGHAI)

    _profiles: ClassVar[tuple[FixtureStockProfile, ...]] = (
        FixtureStockProfile(
            stock=Stock("600519", Market.SH, "贵州茅台", list_date=date(2001, 8, 27)),
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
            daily_base_price=1500.0,
            daily_step=1.35,
            daily_volume_base=2_500_000,
            minute_base_price=1578.0,
            minute_step=0.18,
            minute_volume_base=18_000,
        ),
        FixtureStockProfile(
            stock=Stock("000001", Market.SZ, "平安银行", list_date=date(1991, 4, 3)),
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
            daily_base_price=10.6,
            daily_step=0.012,
            daily_volume_base=35_000_000,
            minute_base_price=11.18,
            minute_step=0.0015,
            minute_volume_base=210_000,
        ),
        FixtureStockProfile(
            stock=Stock("600036", Market.SH, "招商银行", list_date=date(2002, 4, 9)),
            latest_price=34.82,
            change_percent=0.52,
            change_amount=0.18,
            open_price=34.60,
            high_price=35.02,
            low_price=34.42,
            previous_close=34.64,
            volume=31_850_000,
            amount=1_106_000_000.0,
            turnover_rate=0.15,
            total_market_cap=878_000_000_000.0,
            daily_base_price=32.8,
            daily_step=0.035,
            daily_volume_base=23_000_000,
            minute_base_price=34.58,
            minute_step=0.004,
            minute_volume_base=160_000,
        ),
        FixtureStockProfile(
            stock=Stock("601899", Market.SH, "紫金矿业", list_date=date(2008, 4, 25)),
            latest_price=18.76,
            change_percent=1.24,
            change_amount=0.23,
            open_price=18.49,
            high_price=18.93,
            low_price=18.36,
            previous_close=18.53,
            volume=126_400_000,
            amount=2_371_000_000.0,
            turnover_rate=0.48,
            total_market_cap=498_000_000_000.0,
            daily_base_price=16.4,
            daily_step=0.038,
            daily_volume_base=91_000_000,
            minute_base_price=18.42,
            minute_step=0.0048,
            minute_volume_base=430_000,
        ),
        FixtureStockProfile(
            stock=Stock("600988", Market.SH, "赤峰黄金", list_date=date(2004, 4, 14)),
            latest_price=23.45,
            change_percent=3.08,
            change_amount=0.70,
            open_price=22.81,
            high_price=23.68,
            low_price=22.70,
            previous_close=22.75,
            volume=82_600_000,
            amount=1_921_000_000.0,
            turnover_rate=4.96,
            total_market_cap=39_000_000_000.0,
            daily_base_price=20.8,
            daily_step=0.045,
            daily_volume_base=56_000_000,
            minute_base_price=22.92,
            minute_step=0.006,
            minute_volume_base=360_000,
        ),
        FixtureStockProfile(
            stock=Stock("300750", Market.SZ, "宁德时代", list_date=date(2018, 6, 11)),
            latest_price=256.80,
            change_percent=-1.18,
            change_amount=-3.06,
            open_price=260.20,
            high_price=261.10,
            low_price=255.60,
            previous_close=259.86,
            volume=14_210_000,
            amount=3_662_000_000.0,
            turnover_rate=0.36,
            total_market_cap=1_129_000_000_000.0,
            daily_base_price=242.0,
            daily_step=0.22,
            daily_volume_base=10_500_000,
            minute_base_price=259.2,
            minute_step=-0.028,
            minute_volume_base=92_000,
        ),
    )
    _profiles_by_code: ClassVar[dict[str, FixtureStockProfile]] = {
        profile.stock.code: profile for profile in _profiles
    }
    _stocks: ClassVar[tuple[Stock, ...]] = tuple(profile.stock for profile in _profiles)

    async def fetch_stock_master(self) -> list[Stock]:
        return list(self._stocks)

    async def fetch_trading_days(self, start: date, end: date) -> set[date]:
        return {day for day in self._trading_days() if start <= day <= end}

    async def fetch_market_snapshot(self) -> list[QuoteSnapshot]:
        return [
            QuoteSnapshot(
                code=profile.stock.code,
                market=profile.stock.market,
                name=profile.stock.name,
                captured_at=self.captured_at,
                latest_price=profile.latest_price,
                change_percent=profile.change_percent,
                change_amount=profile.change_amount,
                open_price=profile.open_price,
                high_price=profile.high_price,
                low_price=profile.low_price,
                previous_close=profile.previous_close,
                volume=profile.volume,
                amount=profile.amount,
                turnover_rate=profile.turnover_rate,
                total_market_cap=profile.total_market_cap,
                source=self.name,
                quality_status=QualityStatus.OK,
            )
            for profile in self._profiles
        ]

    async def fetch_daily_bars(
        self,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        if code not in self._profiles_by_code:
            return []
        if period not in {"1d", "1w", "1mo"} or adjustment != "qfq":
            return []
        return [
            bar
            for bar in self._history_bars(code, period)
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
        if code not in self._profiles_by_code:
            return []
        if period == "1m" and adjustment == "none":
            bars = self._minute_bars(code)
        elif period in {"5m", "15m", "30m", "60m"} and adjustment == "qfq":
            bars = self._multiperiod_minute_bars(code, period)
        else:
            return []
        return [bar for bar in bars if start <= bar.bar_time <= end]

    @classmethod
    def _trading_days(cls) -> list[date]:
        return [*cls._daily_bar_days(), cls.trade_date]

    @classmethod
    def _daily_bar_days(cls) -> list[date]:
        days: list[date] = []
        cursor = cls.trade_date - timedelta(days=1)
        while len(days) < 60:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor -= timedelta(days=1)
        return list(reversed(days))

    @classmethod
    def _daily_bars(cls, code: str) -> list[Bar]:
        profile = cls._profiles_by_code[code]
        base_price = profile.daily_base_price
        step = profile.daily_step
        volume_base = profile.daily_volume_base
        alternating_move = max(abs(base_price) * 0.0028, 0.04)
        high_padding = max(abs(base_price) * 0.004, 0.05)
        low_padding = max(abs(base_price) * 0.0033, 0.04)
        volume_step = max(volume_base // 180, 10_000)
        result: list[Bar] = []
        trading_days = cls._daily_bar_days()
        for index, trading_day in enumerate(trading_days):
            if code == "600519" and index >= len(trading_days) - 8:
                open_price = 1670.0 if index == len(trading_days) - 8 else 1540.0 + index % 2 * 2
                close_price = open_price
            else:
                open_price = base_price + index * step
                close_price = open_price + (
                    alternating_move if index % 2 == 0 else -alternating_move / 2
                )
            high_price = max(open_price, close_price) + high_padding
            low_price = min(open_price, close_price) - low_padding
            volume = volume_base + index * volume_step
            result.append(
                Bar(
                    code=code,
                    market=profile.stock.market,
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
                    acquired_at=cls.captured_at,
                )
            )
        return result

    @classmethod
    def _history_bars(cls, code: str, period: str) -> list[Bar]:
        daily_bars = cls._daily_bars(code)
        if period == "1d":
            return daily_bars
        grouped: dict[tuple[int, int], list[Bar]] = {}
        for bar in daily_bars:
            if period == "1w":
                iso = bar.bar_time.isocalendar()
                key = (iso.year, iso.week)
            else:
                key = (bar.bar_time.year, bar.bar_time.month)
            grouped.setdefault(key, []).append(bar)

        result: list[Bar] = []
        for bars in grouped.values():
            first, last = bars[0], bars[-1]
            result.append(
                Bar(
                    code=code,
                    market=first.market,
                    period=period,
                    adjustment="qfq",
                    bar_time=last.bar_time,
                    open_price=first.open_price,
                    high_price=max(bar.high_price for bar in bars),
                    low_price=min(bar.low_price for bar in bars),
                    close_price=last.close_price,
                    volume=sum(bar.volume for bar in bars),
                    amount=round(sum(bar.amount for bar in bars), 2),
                    source=cls.name,
                    is_complete=all(bar.is_complete for bar in bars),
                    acquired_at=cls.captured_at,
                )
            )
        return result

    @classmethod
    def _minute_bars(cls, code: str) -> list[Bar]:
        profile = cls._profiles_by_code[code]
        base_price = profile.minute_base_price
        price_step = profile.minute_step
        volume_base = profile.minute_volume_base
        alternating_move = max(abs(base_price) * 0.0002, 0.004)
        high_padding = max(abs(base_price) * 0.0003, 0.006)
        low_padding = max(abs(base_price) * 0.00025, 0.005)
        volume_step = max(volume_base // 240, 90)
        result: list[Bar] = []
        for index in range(61):
            bar_time = datetime.combine(cls.trade_date, time(9, 30), tzinfo=SHANGHAI)
            bar_time += timedelta(minutes=index)
            open_price = base_price + index * price_step
            close_price = open_price + (
                alternating_move if index % 2 == 0 else -alternating_move / 2
            )
            high_price = max(open_price, close_price) + high_padding
            low_price = min(open_price, close_price) - low_padding
            volume = volume_base + index * volume_step
            result.append(
                Bar(
                    code=code,
                    market=profile.stock.market,
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
                    acquired_at=cls.captured_at,
                )
            )
        return result

    @classmethod
    def _multiperiod_minute_bars(cls, code: str, period: str) -> list[Bar]:
        profile = cls._profiles_by_code[code]
        period_minutes = int(period.removesuffix("m"))
        trading_days = [*cls._daily_bar_days()[-10:], cls.trade_date]
        session_times: list[time] = []
        for session_start, session_end in (
            (time(9, 30), time(11, 30)),
            (time(13, 0), time(15, 0)),
        ):
            cursor = datetime.combine(cls.trade_date, session_start, tzinfo=SHANGHAI)
            session_close = datetime.combine(cls.trade_date, session_end, tzinfo=SHANGHAI)
            cursor += timedelta(minutes=period_minutes)
            while cursor <= session_close:
                session_times.append(cursor.time())
                cursor += timedelta(minutes=period_minutes)

        result: list[Bar] = []
        for day_index, trading_day in enumerate(trading_days):
            for bucket_index, bucket_time in enumerate(session_times):
                bar_time = datetime.combine(trading_day, bucket_time, tzinfo=SHANGHAI)
                if trading_day == cls.trade_date and bar_time > cls.captured_at:
                    continue
                index = day_index * len(session_times) + bucket_index
                cycle = index % 16
                wave = cycle if cycle <= 8 else 16 - cycle
                direction = 1 if (index // 8) % 2 == 0 else -1
                open_price = (
                    profile.minute_base_price
                    + day_index * profile.minute_step * 1.5
                    + direction * wave * profile.minute_step * 4
                )
                movement = max(abs(profile.minute_base_price) * 0.0007, 0.008)
                close_price = open_price + (movement if index % 2 == 0 else -movement / 2)
                high_price = max(open_price, close_price) + movement * 0.8
                low_price = min(open_price, close_price) - movement * 0.7
                volume = profile.minute_volume_base * period_minutes + index * 1_000
                is_complete = bar_time < cls.captured_at
                result.append(
                    Bar(
                        code=code,
                        market=profile.stock.market,
                        period=period,
                        adjustment="qfq",
                        bar_time=bar_time,
                        open_price=round(open_price, 3),
                        high_price=round(high_price, 3),
                        low_price=round(low_price, 3),
                        close_price=round(close_price, 3),
                        volume=volume,
                        amount=round(volume * close_price, 2),
                        source=cls.name,
                        is_complete=is_complete,
                        acquired_at=cls.captured_at,
                        quality_status=(
                            QualityStatus.OK if is_complete else QualityStatus.PARTIAL
                        ),
                    )
                )
        return result
