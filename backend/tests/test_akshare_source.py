from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.domain.models import Market, QualityStatus


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
    assert quotes[0].volume == 33455
    assert quotes[0].quality_status is QualityStatus.OK


def test_normalize_snapshot_turns_dash_into_none():
    frame = pd.DataFrame([{"代码": "000001", "名称": "平安银行", "最新价": "-"}])

    quotes = AkshareSource.normalize_snapshot(
        frame, datetime(2026, 8, 4, 10, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    )

    assert quotes[0].latest_price is None
    assert quotes[0].quality_status is QualityStatus.PARTIAL


def test_market_mapping_supports_beijing_exchange():
    assert AkshareSource.market_for_code("920092") is Market.BJ


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

    bars = AkshareSource.normalize_minute_bars("000001", frame, "1m", "none")

    assert bars[0].bar_time.isoformat() == "2026-08-04T10:31:00+08:00"
    assert bars[0].is_complete is True
