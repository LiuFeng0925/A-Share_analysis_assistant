from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

import a_share_radar.data_sources.akshare_source as source_module
from a_share_radar.data_sources.akshare_source import AkshareSource, _number
from a_share_radar.domain.models import Market, QualityStatus

TZ = ZoneInfo("Asia/Shanghai")


def test_normalize_snapshot_maps_chinese_columns():
    frame = pd.DataFrame(
        [
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "最新价": 1330.06,
                "涨跌幅": -2.13,
                "涨跌额": -28.92,
                "今开": 1350.06,
                "最高": 1350.94,
                "最低": 1330.04,
                "昨收": 1358.98,
                "成交量": 33455,
                "成交额": 4472998836.0,
                "换手率": 0.27,
                "总市值": 1670000000000.0,
            }
        ]
    )
    captured_at = datetime(2026, 8, 4, 10, 31, tzinfo=ZoneInfo("Asia/Shanghai"))

    quotes = AkshareSource.normalize_snapshot(frame, captured_at)

    assert len(quotes) == 1
    assert quotes[0].market is Market.SH
    assert quotes[0].code == "600519"
    assert quotes[0].latest_price == 1330.06
    assert quotes[0].volume == 3_345_500
    assert quotes[0].quality_status is QualityStatus.OK


def test_normalize_snapshot_turns_dash_into_none():
    frame = pd.DataFrame([{"代码": "000001", "名称": "平安银行", "最新价": "-"}])

    quotes = AkshareSource.normalize_snapshot(
        frame, datetime(2026, 8, 4, 10, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert quotes[0].latest_price is None
    assert quotes[0].quality_status is QualityStatus.PARTIAL


def test_number_turns_pandas_missing_value_into_none():
    assert _number(pd.NA) is None


def test_market_mapping_supports_beijing_exchange():
    assert AkshareSource.market_for_code("920092") is Market.BJ


def test_normalize_tencent_snapshot_maps_prefixed_stock_codes_and_core_fields():
    frame = pd.DataFrame(
        [
            {
                "code": "sh600519",
                "name": "贵州茅台",
                "zxj": "1300.60",
                "zdf": "-0.45",
                "zd": "-5.85",
                "volume": "8257.00",
                "turnover": "107725",
                "hsl": "0.07",
                "zsz": "16258.56",
            }
        ]
    )
    captured_at = datetime(2026, 8, 6, 10, 8, tzinfo=TZ)

    quotes = AkshareSource.normalize_tencent_snapshot(frame, captured_at)

    assert len(quotes) == 1
    assert quotes[0].market is Market.SH
    assert quotes[0].code == "600519"
    assert quotes[0].name == "贵州茅台"
    assert quotes[0].latest_price == 1300.60
    assert quotes[0].change_percent == -0.45
    assert quotes[0].change_amount == -5.85
    assert quotes[0].volume == 825_700
    assert quotes[0].amount == 1_077_250_000
    assert quotes[0].turnover_rate == 0.07
    assert quotes[0].total_market_cap == 1_625_856_000_000
    assert quotes[0].source == "akshare-tencent"
    assert quotes[0].quality_status is QualityStatus.PARTIAL


async def test_fetch_market_snapshot_falls_back_to_tencent_when_eastmoney_disconnects(
    monkeypatch,
):
    def fail_eastmoney():
        raise requests.ConnectionError("东方财富临时断连")

    def fetch_tencent():
        return pd.DataFrame(
            [
                {
                    "code": "sz000001",
                    "name": "平安银行",
                    "zxj": "11.30",
                    "zdf": "0.44",
                    "zd": "0.05",
                    "volume": "1510000",
                    "turnover": "170400",
                    "hsl": "0.78",
                    "zsz": "2183.17",
                }
            ]
        )

    monkeypatch.setattr(source_module.ak, "stock_zh_a_spot_em", fail_eastmoney)
    monkeypatch.setattr(source_module.ak, "stock_zh_a_spot_tx", fetch_tencent)

    quotes = await AkshareSource().fetch_market_snapshot(timeout_seconds=3)

    assert len(quotes) == 1
    assert quotes[0].market is Market.SZ
    assert quotes[0].code == "000001"
    assert quotes[0].source == "akshare-tencent"


def test_minute_bar_keeps_provider_time():
    frame = pd.DataFrame(
        [
            {
                "时间": "2026-08-04 10:31:00",
                "开盘": 10.1,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1000,
                "成交额": 10200.0,
            }
        ]
    )

    acquired_at = datetime(2026, 8, 4, 10, 31, 30, tzinfo=TZ)
    bars = AkshareSource.normalize_minute_bars(
        "000001", frame, "1m", "none", acquired_at=acquired_at
    )

    assert bars[0].bar_time.isoformat() == "2026-08-04T10:31:00+08:00"
    assert bars[0].volume == 100_000
    assert bars[0].acquired_at == acquired_at
    assert bars[0].quality_status is QualityStatus.OK
    assert bars[0].is_complete is True


def test_history_bar_converts_lots_to_shares_and_records_acquisition_time():
    frame = pd.DataFrame(
        [
            {
                "日期": "2026-08-03",
                "开盘": 10.1,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            }
        ]
    )
    acquired_at = datetime(2026, 8, 4, 15, 10, tzinfo=TZ)

    bars = AkshareSource.normalize_history_bars(
        "000001", frame, "1d", "qfq", acquired_at=acquired_at
    )

    assert bars[0].volume == 100_000
    assert bars[0].acquired_at == acquired_at
    assert bars[0].quality_status is QualityStatus.OK


def test_minute_normalization_filters_zero_ohlc_bad_bar():
    frame = pd.DataFrame(
        [
            {
                "时间": "2026-08-04 10:30:00",
                "开盘": 0,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            },
            {
                "时间": "2026-08-04 10:31:00",
                "开盘": 10.1,
                "收盘": 10.2,
                "最高": 10.3,
                "最低": 10.0,
                "成交量": 1_000,
                "成交额": 1_020_000.0,
            },
        ]
    )

    bars = AkshareSource.normalize_minute_bars(
        "000001",
        frame,
        "1m",
        "none",
        acquired_at=datetime(2026, 8, 4, 10, 33, tzinfo=TZ),
    )

    assert [bar.bar_time.minute for bar in bars] == [31]
    assert bars[0].quality_status is QualityStatus.PARTIAL
