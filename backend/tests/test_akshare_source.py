from datetime import date, datetime
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


async def test_fetch_daily_bars_uses_tencent_daily_source(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def fetch_tencent(**kwargs):
        calls.append(("tencent", kwargs))
        return pd.DataFrame(
            [
                {
                    "date": "2026-08-06",
                    "open": 43.53,
                    "close": 41.30,
                    "high": 43.60,
                    "low": 41.08,
                    "volume": 88_290_100.0,
                    "turnover": 0.0531,
                    "amount": 3_721_211_700.0,
                }
            ]
        )

    def fail_eastmoney(**kwargs):
        calls.append(("eastmoney", kwargs))
        raise AssertionError("日 K 应优先使用腾讯源")

    monkeypatch.setattr(source_module.ak, "stock_zh_a_hist_tx", fetch_tencent)
    monkeypatch.setattr(source_module.ak, "stock_zh_a_hist", fail_eastmoney)

    batch = await AkshareSource().fetch_daily_bars(
        "600988", date(2026, 8, 5), date(2026, 8, 6), "1d", "qfq"
    )

    assert [name for name, _ in calls] == ["tencent"]
    assert batch.source == "akshare-tencent"
    assert batch.bars[0].bar_time.date() == date(2026, 8, 6)
    assert batch.bars[0].volume == 88_290_100
    assert batch.bars[0].amount == 3_721_211_700.0


async def test_fetch_minute_bars_uses_sina_minute_source(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    def fetch_sina(**kwargs):
        calls.append(("sina", kwargs))
        return pd.DataFrame(
            [
                {
                    "day": "2026-08-06 15:00:00",
                    "open": 41.40,
                    "high": 41.49,
                    "low": 41.21,
                    "close": 41.30,
                    "volume": 1_136_000.0,
                    "amount": 46_920_000.0,
                }
            ]
        )

    def fail_eastmoney(**kwargs):
        calls.append(("eastmoney", kwargs))
        raise AssertionError("分钟 K 应优先使用新浪源")

    monkeypatch.setattr(source_module.ak, "stock_zh_a_minute", fetch_sina)
    monkeypatch.setattr(source_module.ak, "stock_zh_a_hist_min_em", fail_eastmoney)

    batch = await AkshareSource().fetch_minute_bars(
        "600988",
        datetime(2026, 8, 6, 9, 30, tzinfo=TZ),
        datetime(2026, 8, 6, 15, 0, tzinfo=TZ),
        "15m",
        "none",
    )

    assert [name for name, _ in calls] == ["sina"]
    assert batch.source == "akshare-sina"
    assert batch.bars[0].bar_time == datetime(2026, 8, 6, 15, 0, tzinfo=TZ)
    assert batch.bars[0].source == "akshare-sina"
    assert batch.bars[0].volume == 1_136_000


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


def test_tencent_daily_history_infers_lot_volume_from_amount_when_needed():
    frame = pd.DataFrame(
        [
            {
                "date": "2026-08-07",
                "open": 5.23,
                "close": 5.40,
                "high": 5.40,
                "low": 5.21,
                "volume": 235_371.0,
                "turnover": 0.0395,
                "amount": 125_377_300.0,
            },
            {
                "date": "2026-08-07",
                "open": 41.02,
                "close": 42.09,
                "high": 42.09,
                "low": 40.20,
                "volume": 77_441_700.0,
                "turnover": 0.0465,
                "amount": 3_195_898_000.0,
            },
        ]
    )

    bars = AkshareSource.normalize_history_bars(
        "000788",
        frame,
        "1d",
        "qfq",
        acquired_at=datetime(2026, 8, 7, 17, 0, tzinfo=TZ),
        source="akshare-tencent",
        volume_is_lots=False,
    )

    assert bars[0].volume == 23_537_100
    assert bars[1].volume == 77_441_700


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
