from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from a_share_radar.config import Settings
from a_share_radar.main import create_app

TZ = ZoneInfo("Asia/Shanghai")


async def get(app, path, *, params=None, headers=None):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, params=params, headers=headers)


async def test_market_summary_returns_all_eight_fields_and_uses_injected_clock(
    app_with_fixture_data,
):
    class OpenClock:
        @staticmethod
        def is_open(at):
            return True

    app_with_fixture_data.state.clock = OpenClock()

    response = await get(app_with_fixture_data, "/api/market/summary")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "total",
        "rising",
        "falling",
        "flat",
        "amount",
        "market_status",
        "last_updated_at",
        "stale",
    }
    assert body["total"] == 2
    assert body["rising"] == 1
    assert body["falling"] == 1
    assert body["flat"] == 0
    assert body["amount"] == 4597998836.0
    assert body["market_status"] == "open"
    assert body["last_updated_at"].endswith("+08:00")
    assert isinstance(body["stale"], bool)


async def test_stock_list_search_and_pagination(app_with_fixture_data):
    response = await get(
        app_with_fixture_data,
        "/api/market/stocks",
        params={"query": "茅台", "page": 1, "page_size": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["items"][0]["code"] == "600519"
    assert body["items"][0]["market"] == "SH"


async def test_stock_list_filters_market_and_sorts_quotes(app_with_fixture_data):
    market_response = await get(
        app_with_fixture_data,
        "/api/market/stocks",
        params={"market": "SZ", "page_size": 10},
    )
    sorted_response = await get(
        app_with_fixture_data,
        "/api/market/stocks",
        params={"sort_by": "latest_price", "sort_order": "asc", "page_size": 10},
    )

    assert market_response.status_code == 200
    body = market_response.json()
    assert body["total"] == 1
    assert [item["code"] for item in body["items"]] == ["000001"]
    assert body["items"][0]["quality_status"] == "ok"
    assert body["items"][0]["captured_at"].endswith("+08:00")
    assert sorted_response.status_code == 200
    assert [item["code"] for item in sorted_response.json()["items"]] == [
        "000001",
        "600519",
    ]


async def test_stock_list_can_return_an_empty_later_page(app_with_fixture_data):
    response = await get(
        app_with_fixture_data,
        "/api/market/stocks",
        params={"page": 2, "page_size": 10},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 2, "page": 2, "page_size": 10}


async def test_stock_detail_returns_latest_quote(app_with_fixture_data):
    response = await get(app_with_fixture_data, "/api/stocks/SH/600519")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "600519"
    assert body["market"] == "SH"
    assert body["name"] == "贵州茅台"
    assert body["latest_price"] == 1330.06
    assert body["previous_close"] == 1358.98
    assert body["turnover_rate"] == 0.27
    assert body["total_market_cap"] == 1670000000000.0
    assert body["volume"] == 33455
    assert body["amount"] == 4472998836.0
    assert body["captured_at"].endswith("+08:00")
    assert body["quality_status"] == "ok"


async def test_stock_detail_returns_404_when_stock_does_not_exist(
    app_with_fixture_data,
):
    response = await get(app_with_fixture_data, "/api/stocks/SH/999999")

    assert response.status_code == 404
    assert response.json() == {"detail": "未找到该股票"}


async def test_today_bars_return_one_minute_semantics(app_with_fixture_data):
    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/bars",
        params={"period": "1m", "range": "today", "adjustment": "none"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["market"] == "SH"
    assert body["code"] == "600519"
    assert body["period"] == "1m"
    assert body["range"] == "today"
    assert body["adjustment"] == "none"
    assert body["source"] == "fixture"
    assert body["last_updated_at"].endswith("+08:00")
    assert body["items"][0]["bar_time"].endswith("+08:00")
    assert body["items"][0]["acquired_at"].endswith("+08:00")
    assert body["items"][0]["quality_status"] == "partial"
    assert body["items"][0]["is_complete"] is False


async def test_today_one_minute_bars_default_to_unadjusted(app_with_fixture_data):
    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/bars",
        params={"period": "1m", "range": "today"},
    )

    assert response.status_code == 200
    assert response.json()["adjustment"] == "none"


async def test_bar_series_last_update_uses_acquisition_time(
    app_with_fixture_data, fake_source
):
    acquired_at = datetime(2026, 8, 4, 10, 31, 30, tzinfo=TZ)
    fake_source.bar_rows[0] = replace(
        fake_source.bar_rows[0], acquired_at=acquired_at
    )

    response = await get(
        app_with_fixture_data,
        "/api/stocks/SH/600519/bars",
        params={"period": "1m", "range": "today", "adjustment": "none"},
    )

    assert response.status_code == 200
    assert response.json()["last_updated_at"] == acquired_at.isoformat()


async def test_data_status_returns_storage_counts(app_with_fixture_data):
    response = await get(app_with_fixture_data, "/api/system/data-status")

    assert response.status_code == 200
    body = response.json()
    assert {
        key: body[key]
        for key in (
            "stock_count",
            "latest_quote_count",
            "snapshot_count",
            "bar_count",
            "latest_captured_at",
        )
    } == {
        "stock_count": 2,
        "latest_quote_count": 2,
        "snapshot_count": 2,
        "bar_count": 2,
        "latest_captured_at": "2026-08-04T10:31:00+08:00",
    }
    assert {
        "latest_success_at",
        "latest_failure_at",
        "latest_market_time",
        "snapshot_expected_count",
        "snapshot_actual_count",
        "snapshot_coverage_ratio",
        "snapshot_quality_status",
    } <= set(body)


@pytest.mark.parametrize(
    "params",
    [
        {"sort_by": "drop table"},
        {"sort_order": "sideways"},
        {"market": "NYSE"},
        {"page": 0},
        {"page_size": 9},
        {"page_size": 201},
    ],
)
async def test_stock_list_rejects_invalid_query_values_with_422(
    app_with_fixture_data, params
):
    response = await get(app_with_fixture_data, "/api/market/stocks", params=params)

    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"
    assert response.json()["errors"]
    assert all(
        error["message"] == "参数值不合法" for error in response.json()["errors"]
    )


@pytest.mark.parametrize("market", ["NYSE", "sh"])
async def test_stock_path_rejects_invalid_market_with_422(
    app_with_fixture_data, market
):
    response = await get(app_with_fixture_data, f"/api/stocks/{market}/600519")

    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"
    assert response.json()["errors"] == [
        {"location": ["path", "market"], "message": "参数值不合法"}
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"period": "2m", "range": "today", "adjustment": "none"},
        {"period": "1m", "range": "unknown", "adjustment": "none"},
        {"period": "1d", "range": "5d", "adjustment": "bad"},
    ],
)
async def test_bars_reject_invalid_enum_values_with_422(
    app_with_fixture_data, params
):
    response = await get(
        app_with_fixture_data, "/api/stocks/SH/600519/bars", params=params
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"
    assert response.json()["errors"]
    assert all(
        error["message"] == "参数值不合法" for error in response.json()["errors"]
    )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (
            {"period": "1d", "range": "today", "adjustment": "none"},
            "今日视图只允许一分钟 K",
        ),
        (
            {"period": "1m", "range": "5d", "adjustment": "qfq"},
            "免费一分钟 K 只允许不复权",
        ),
    ],
)
async def test_bars_map_invalid_combinations_to_422_with_chinese_detail(
    app_with_fixture_data, params, message
):
    response = await get(
        app_with_fixture_data, "/api/stocks/SH/600519/bars", params=params
    )

    assert response.status_code == 422
    assert response.json() == {"detail": message}


async def test_upstream_value_error_returns_empty_bars_without_leaking_detail(
    app_with_fixture_data, fake_source
):
    fake_source.minute_error = ValueError("上游字段转换失败：敏感原文")
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fixture_data, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/stocks/SZ/000001/bars",
            params={"period": "1m", "range": "today"},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["items"] == []
    assert body["fetch_quality_status"] == "error"
    assert body["fetch_raw_row_count"] == 0
    assert "敏感原文" not in response.text


async def test_response_validation_error_remains_a_server_error(
    app_with_fixture_data, monkeypatch
):
    def invalid_data_status():
        return {
            "stock_count": "不是整数",
            "latest_quote_count": 2,
            "snapshot_count": 2,
            "bar_count": 2,
            "latest_captured_at": None,
        }

    monkeypatch.setattr(
        app_with_fixture_data.state.repository,
        "data_status",
        invalid_data_status,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fixture_data, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/system/data-status")

    assert response.status_code == 500
    assert response.json() == {"detail": "系统内部错误"}


async def test_cors_allows_only_exact_local_frontend_origins(app_with_fixture_data):
    allowed_origins = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    for origin in allowed_origins:
        response = await get(
            app_with_fixture_data,
            "/api/health",
            headers={"Origin": origin},
        )
        assert response.headers["access-control-allow-origin"] == origin

    for rejected_origin in ["http://127.0.0.1:4173", "http://localhost:4173"]:
        response = await get(
            app_with_fixture_data,
            "/api/health",
            headers={"Origin": rejected_origin},
        )
        assert "access-control-allow-origin" not in response.headers


async def test_cors_uses_configured_frontend_port_for_both_local_hosts(
    tmp_path, fake_source
):
    app = create_app(
        settings=Settings(data_dir=tmp_path, frontend_port=15173),
        source=fake_source,
    )

    for origin in ["http://127.0.0.1:15173", "http://localhost:15173"]:
        response = await get(app, "/api/health", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin

    response = await get(
        app, "/api/health", headers={"Origin": "http://127.0.0.1:5173"}
    )
    assert "access-control-allow-origin" not in response.headers


async def test_cors_preflight_allows_get_and_rejects_post(app_with_fixture_data):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_fixture_data), base_url="http://test"
    ) as client:
        get_response = await client.options(
            "/api/market/stocks",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        post_response = await client.options(
            "/api/market/stocks",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert get_response.status_code == 200
    assert get_response.headers["access-control-allow-methods"] == "GET"
    assert post_response.status_code == 400


def test_response_models_validate_dataclass_enum_and_aware_datetime(fake_source):
    from a_share_radar.api.schemas import BarResponse, StockQuoteResponse

    bar = BarResponse.model_validate(fake_source.bar_rows[0], from_attributes=True)

    assert bar.bar_time.utcoffset() is not None
    assert StockQuoteResponse.model_validate(
        fake_source.snapshot_rows[0], from_attributes=True
    ).market.value == "SH"

    with pytest.raises(ValidationError, match="timezone"):
        BarResponse.model_validate(
            {
                "bar_time": datetime(2026, 8, 4, 10, 31, tzinfo=UTC).replace(
                    tzinfo=None
                ),
                "open_price": 1.0,
                "high_price": 1.0,
                "low_price": 1.0,
                "close_price": 1.0,
                "volume": 1,
                "amount": 1.0,
                "is_complete": True,
            }
        )
