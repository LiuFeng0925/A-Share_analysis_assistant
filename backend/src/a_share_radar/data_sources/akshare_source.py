import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from a_share_radar.domain.models import Bar, Market, QualityStatus, QuoteSnapshot, Stock

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value: object) -> float | None:
    if value is None or pd.isna(value) or value == "-":
        return None
    return float(value)


def _integer(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


class AkshareSource:
    name = "akshare-eastmoney"

    @staticmethod
    def market_for_code(code: str) -> Market:
        if code.startswith(("4", "8", "9")):
            return Market.BJ
        if code.startswith(("5", "6", "7")):
            return Market.SH
        return Market.SZ

    @classmethod
    def normalize_snapshot(
        cls, frame: pd.DataFrame, captured_at: datetime
    ) -> list[QuoteSnapshot]:
        result: list[QuoteSnapshot] = []
        for row in frame.to_dict("records"):
            latest = _number(row.get("最新价"))
            result.append(
                QuoteSnapshot(
                    code=str(row["代码"]).zfill(6),
                    market=cls.market_for_code(str(row["代码"])),
                    name=str(row["名称"]),
                    captured_at=captured_at,
                    latest_price=latest,
                    change_percent=_number(row.get("涨跌幅")),
                    change_amount=_number(row.get("涨跌额")),
                    open_price=_number(row.get("今开")),
                    high_price=_number(row.get("最高")),
                    low_price=_number(row.get("最低")),
                    previous_close=_number(row.get("昨收")),
                    volume=_integer(row.get("成交量")),
                    amount=_number(row.get("成交额")),
                    turnover_rate=_number(row.get("换手率")),
                    total_market_cap=_number(row.get("总市值")),
                    source=cls.name,
                    quality_status=(
                        QualityStatus.OK if latest is not None else QualityStatus.PARTIAL
                    ),
                )
            )
        return result

    @classmethod
    def normalize_minute_bars(
        cls, code: str, frame: pd.DataFrame, period: str, adjustment: str
    ) -> list[Bar]:
        return [
            Bar(
                code=code,
                market=cls.market_for_code(code),
                period=period,
                adjustment=adjustment,
                bar_time=pd.Timestamp(row["时间"]).to_pydatetime().replace(tzinfo=SHANGHAI),
                open_price=float(row["开盘"]),
                high_price=float(row["最高"]),
                low_price=float(row["最低"]),
                close_price=float(row["收盘"]),
                volume=int(row["成交量"]),
                amount=float(row["成交额"]),
                source=cls.name,
                is_complete=True,
            )
            for row in frame.to_dict("records")
        ]

    async def fetch_market_snapshot(self) -> list[QuoteSnapshot]:
        frame = await asyncio.to_thread(ak.stock_zh_a_spot_em)
        return self.normalize_snapshot(frame, datetime.now(SHANGHAI))

    async def fetch_stock_master(self) -> list[Stock]:
        quotes = await self.fetch_market_snapshot()
        return [Stock(quote.code, quote.market, quote.name) for quote in quotes]

    async def fetch_trading_days(self, start: date, end: date) -> set[date]:
        frame = await asyncio.to_thread(ak.tool_trade_date_hist_sina)
        values = pd.to_datetime(frame["trade_date"]).dt.date
        return {value for value in values if start <= value <= end}

    @classmethod
    def normalize_history_bars(
        cls,
        code: str,
        frame: pd.DataFrame,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        return [
            Bar(
                code=code,
                market=cls.market_for_code(code),
                period=period,
                adjustment=adjustment,
                bar_time=datetime.combine(
                    pd.Timestamp(row["日期"]).date(), time(15, 0), tzinfo=SHANGHAI
                ),
                open_price=float(row["开盘"]),
                high_price=float(row["最高"]),
                low_price=float(row["最低"]),
                close_price=float(row["收盘"]),
                volume=int(row["成交量"]),
                amount=float(row["成交额"]),
                source=cls.name,
                is_complete=True,
            )
            for row in frame.to_dict("records")
        ]

    async def fetch_daily_bars(
        self,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        provider_period = {"1d": "daily", "1w": "weekly", "1mo": "monthly"}[period]
        provider_adjustment = "" if adjustment == "none" else adjustment
        frame = await asyncio.to_thread(
            ak.stock_zh_a_hist,
            symbol=code,
            period=provider_period,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=provider_adjustment,
        )
        return self.normalize_history_bars(code, frame, period, adjustment)

    async def fetch_minute_bars(
        self,
        code: str,
        start: datetime,
        end: datetime,
        period: str,
        adjustment: str,
    ) -> list[Bar]:
        provider_adjustment = "" if adjustment == "none" else adjustment
        frame = await asyncio.to_thread(
            ak.stock_zh_a_hist_min_em,
            symbol=code,
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
            period=period.removesuffix("m"),
            adjust=provider_adjustment,
        )
        return self.normalize_minute_bars(code, frame, period, adjustment)
