import asyncio
import logging
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

from a_share_radar.domain.bar_completion import bar_is_complete
from a_share_radar.domain.models import (
    Bar,
    BarFetchBatch,
    Market,
    QualityStatus,
    QuoteSnapshot,
    Stock,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
_PROVISIONAL_CAPTURED_AT = datetime(1970, 1, 1, tzinfo=SHANGHAI)
logger = logging.getLogger(__name__)


async def _run_provider_thread[Result](
    function: Callable[..., Result], *args: object, **kwargs: object
) -> Result:
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await asyncio.wait({worker})
        if error := worker.exception():
            logger.error("取消期间 AKShare 线程执行失败", exc_info=error)
        raise


def _number(value: object) -> float | None:
    if value is None or pd.isna(value) or value == "-":
        return None
    return float(value)


def _integer(value: object) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _shares_from_lots(value: object) -> int | None:
    lots = _integer(value)
    return None if lots is None else lots * 100


def _valid_ohlc(open_price: float, high: float, low: float, close: float) -> bool:
    values = (open_price, high, low, close)
    return (
        all(math.isfinite(value) and value > 0 for value in values)
        and low <= open_price <= high
        and low <= close <= high
    )


def _restamp_bars(bars: list[Bar], acquired_at: datetime) -> list[Bar]:
    stamped: list[Bar] = []
    for bar in bars:
        complete = bar_is_complete(
            bar.period, bar.bar_time, acquired_at.astimezone(SHANGHAI)
        )
        stamped.append(
            replace(
                bar,
                acquired_at=acquired_at,
                is_complete=complete,
                quality_status=(
                    bar.quality_status if complete else QualityStatus.PARTIAL
                ),
            )
        )
    return stamped


def _bar_batch(
    bars: list[Bar], acquired_at: datetime, source: str, raw_row_count: int
) -> BarFetchBatch:
    invalid_row_count = max(0, raw_row_count - len(bars))
    quality_status = (
        QualityStatus.PARTIAL
        if invalid_row_count > 0
        or any(bar.quality_status is not QualityStatus.OK for bar in bars)
        else QualityStatus.OK
    )
    return BarFetchBatch(
        bars=tuple(bars),
        acquired_at=acquired_at,
        source=source,
        quality_status=quality_status,
        raw_row_count=raw_row_count,
        invalid_row_count=invalid_row_count,
    )


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
    def normalize_snapshot(cls, frame: pd.DataFrame, captured_at: datetime) -> list[QuoteSnapshot]:
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
                    volume=_shares_from_lots(row.get("成交量")),
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
        cls,
        code: str,
        frame: pd.DataFrame,
        period: str,
        adjustment: str,
        acquired_at: datetime | None = None,
    ) -> list[Bar]:
        resolved_acquired_at = acquired_at or datetime.now(SHANGHAI)
        result: list[Bar] = []
        batch_is_partial = False
        for row in frame.to_dict("records"):
            try:
                bar_time = pd.Timestamp(row["时间"]).to_pydatetime().replace(
                    tzinfo=SHANGHAI
                )
                open_price = float(row["开盘"])
                high_price = float(row["最高"])
                low_price = float(row["最低"])
                close_price = float(row["收盘"])
                volume = _shares_from_lots(row["成交量"])
                amount = _number(row["成交额"])
                if (
                    volume is None
                    or volume < 0
                    or amount is None
                    or not math.isfinite(amount)
                    or amount < 0
                ):
                    raise ValueError("成交量或成交额不合法")
            except (KeyError, TypeError, ValueError, OverflowError):
                logger.warning("过滤分钟 K 线字段异常：%s %s", code, row.get("时间"))
                batch_is_partial = True
                continue
            if not _valid_ohlc(open_price, high_price, low_price, close_price):
                logger.warning("过滤分钟 K 线坏柱：%s %s", code, bar_time.isoformat())
                batch_is_partial = True
                continue
            is_complete = bar_is_complete(
                period, bar_time, resolved_acquired_at.astimezone(SHANGHAI)
            )
            result.append(
                Bar(
                    code=code,
                    market=cls.market_for_code(code),
                    period=period,
                    adjustment=adjustment,
                    bar_time=bar_time,
                    open_price=open_price,
                    high_price=high_price,
                    low_price=low_price,
                    close_price=close_price,
                    volume=volume,
                    amount=amount,
                    source=cls.name,
                    is_complete=is_complete,
                    acquired_at=resolved_acquired_at,
                    quality_status=(QualityStatus.OK if is_complete else QualityStatus.PARTIAL),
                )
            )
        if batch_is_partial:
            return [replace(bar, quality_status=QualityStatus.PARTIAL) for bar in result]
        return result

    async def fetch_market_snapshot(self) -> list[QuoteSnapshot]:
        frame = await _run_provider_thread(ak.stock_zh_a_spot_em)
        normalized = self.normalize_snapshot(frame, _PROVISIONAL_CAPTURED_AT)
        captured_at = datetime.now(SHANGHAI)
        return [replace(quote, captured_at=captured_at) for quote in normalized]

    async def fetch_stock_master(self) -> list[Stock]:
        frames = await asyncio.gather(
            self._fetch_listing_frame(
                "上交所主板", ak.stock_info_sh_name_code, "主板A股"
            ),
            self._fetch_listing_frame(
                "上交所科创板", ak.stock_info_sh_name_code, "科创板"
            ),
            self._fetch_listing_frame(
                "深交所 A 股", ak.stock_info_sz_name_code, "A股列表"
            ),
            self._fetch_listing_frame("北交所", ak.stock_info_bj_name_code),
        )
        return self._stock_master(frames)

    @staticmethod
    async def _fetch_listing_frame(
        label: str, function: Callable[..., pd.DataFrame], *args: object
    ) -> pd.DataFrame:
        try:
            return await _run_provider_thread(function, *args)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("加载%s上市日期失败，保留仓储已有日期", label)
            return pd.DataFrame()

    @staticmethod
    def _stock_master(frames: list[pd.DataFrame]) -> list[Stock]:
        result: dict[tuple[Market, str], Stock] = {}
        specifications = (
            (frames[0], "证券代码", "证券简称", "上市日期"),
            (frames[1], "证券代码", "证券简称", "上市日期"),
            (frames[2], "A股代码", "A股简称", "A股上市日期"),
            (frames[3], "证券代码", "证券简称", "上市日期"),
        )
        for frame, code_column, name_column, date_column in specifications:
            required_columns = {code_column, name_column}
            if not required_columns <= set(frame.columns):
                continue
            selected_columns = [code_column, name_column]
            if date_column in frame:
                selected_columns.append(date_column)
            for row in frame[selected_columns].to_dict("records"):
                raw_code = row[code_column]
                raw_name = row[name_column]
                if pd.isna(raw_code) or pd.isna(raw_name):
                    continue
                code = str(raw_code).split(".", maxsplit=1)[0].zfill(6)
                if len(code) != 6 or not code.isascii() or not code.isdigit():
                    continue
                raw_date = row.get(date_column)
                list_date = (
                    None
                    if raw_date is None or pd.isna(raw_date)
                    else pd.Timestamp(raw_date).date()
                )
                market = AkshareSource.market_for_code(code)
                result[(market, code)] = Stock(
                    code=code,
                    market=market,
                    name=str(raw_name),
                    list_date=list_date,
                )
        return sorted(result.values(), key=lambda stock: (stock.market.value, stock.code))

    async def fetch_trading_days(self, start: date, end: date) -> set[date]:
        frame = await _run_provider_thread(ak.tool_trade_date_hist_sina)
        values = pd.to_datetime(frame["trade_date"]).dt.date
        return {value for value in values if start <= value <= end}

    @classmethod
    def normalize_history_bars(
        cls,
        code: str,
        frame: pd.DataFrame,
        period: str,
        adjustment: str,
        acquired_at: datetime | None = None,
    ) -> list[Bar]:
        resolved_acquired_at = acquired_at or datetime.now(SHANGHAI)
        result: list[Bar] = []
        batch_is_partial = False
        for row in frame.to_dict("records"):
            try:
                bar_time = datetime.combine(
                    pd.Timestamp(row["日期"]).date(), time(15, 0), tzinfo=SHANGHAI
                )
                open_price = float(row["开盘"])
                high_price = float(row["最高"])
                low_price = float(row["最低"])
                close_price = float(row["收盘"])
                volume = _shares_from_lots(row["成交量"])
                amount = _number(row["成交额"])
                if (
                    volume is None
                    or volume < 0
                    or amount is None
                    or not math.isfinite(amount)
                    or amount < 0
                ):
                    raise ValueError("成交量或成交额不合法")
            except (KeyError, TypeError, ValueError, OverflowError):
                logger.warning("过滤历史 K 线字段异常：%s %s", code, row.get("日期"))
                batch_is_partial = True
                continue
            if not _valid_ohlc(open_price, high_price, low_price, close_price):
                logger.warning("过滤历史 K 线坏柱：%s %s", code, bar_time.date())
                batch_is_partial = True
                continue
            is_complete = bar_is_complete(
                period, bar_time, resolved_acquired_at.astimezone(SHANGHAI)
            )
            result.append(
                Bar(
                    code=code,
                    market=cls.market_for_code(code),
                    period=period,
                    adjustment=adjustment,
                    bar_time=bar_time,
                    open_price=open_price,
                    high_price=high_price,
                    low_price=low_price,
                    close_price=close_price,
                    volume=volume,
                    amount=amount,
                    source=cls.name,
                    is_complete=is_complete,
                    acquired_at=resolved_acquired_at,
                    quality_status=(QualityStatus.OK if is_complete else QualityStatus.PARTIAL),
                )
            )
        if batch_is_partial:
            return [replace(bar, quality_status=QualityStatus.PARTIAL) for bar in result]
        return result

    async def fetch_daily_bars(
        self,
        code: str,
        start: date,
        end: date,
        period: str,
        adjustment: str,
    ) -> BarFetchBatch:
        provider_period = {"1d": "daily", "1w": "weekly", "1mo": "monthly"}[period]
        provider_adjustment = "" if adjustment == "none" else adjustment
        frame = await _run_provider_thread(
            ak.stock_zh_a_hist,
            symbol=code,
            period=provider_period,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust=provider_adjustment,
        )
        provisional_at = datetime.combine(end, time(23, 59, 59), tzinfo=SHANGHAI)
        normalized = self.normalize_history_bars(
            code, frame, period, adjustment, acquired_at=provisional_at
        )
        acquired_at = datetime.now(SHANGHAI)
        return _bar_batch(
            _restamp_bars(normalized, acquired_at),
            acquired_at,
            self.name,
            len(frame),
        )

    async def fetch_minute_bars(
        self,
        code: str,
        start: datetime,
        end: datetime,
        period: str,
        adjustment: str,
    ) -> BarFetchBatch:
        provider_adjustment = "" if adjustment == "none" else adjustment
        frame = await _run_provider_thread(
            ak.stock_zh_a_hist_min_em,
            symbol=code,
            start_date=start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end.strftime("%Y-%m-%d %H:%M:%S"),
            period=period.removesuffix("m"),
            adjust=provider_adjustment,
        )
        normalized = self.normalize_minute_bars(
            code, frame, period, adjustment, acquired_at=end.astimezone(SHANGHAI)
        )
        acquired_at = datetime.now(SHANGHAI)
        return _bar_batch(
            _restamp_bars(normalized, acquired_at),
            acquired_at,
            self.name,
            len(frame),
        )
