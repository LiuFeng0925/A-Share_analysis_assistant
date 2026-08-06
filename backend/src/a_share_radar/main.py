import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from a_share_radar.api.routes import router
from a_share_radar.config import Settings
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.data_sources.fixture_source import FixtureSource
from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.models import Bar, BarFetchBatch, Stock
from a_share_radar.services.bar_service import BarService, HistoryBootstrapper
from a_share_radar.services.indicator_service import IndicatorService
from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.scheduler import create_scheduler, shutdown_scheduler
from a_share_radar.services.snapshot_collector import SnapshotCollector
from a_share_radar.storage.archive import archive_pending_snapshots
from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_STOCK_MASTER_MINIMUM_COUNT = 4000


async def _run_blocking_safely(function, *args, **kwargs):
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await worker
        raise


def _batch_bars(result: list[Bar] | BarFetchBatch) -> list[Bar]:
    return list(result.bars) if isinstance(result, BarFetchBatch) else result


def _validated_stock_master(
    stocks: list[Stock], minimum_expected_count: int
) -> list[Stock]:
    if len(stocks) < minimum_expected_count:
        raise RuntimeError(
            f"股票主数据数量异常：实际 {len(stocks)}，低于 {minimum_expected_count}"
        )
    return stocks


async def _persist_fixture_data(
    source: MarketDataSource, repository: MarketRepository
) -> tuple[set[date], datetime]:
    stocks = await source.fetch_stock_master()
    snapshots = await source.fetch_market_snapshot()
    if not snapshots:
        raise RuntimeError("固定数据源必须提供至少一条行情快照")
    fixture_now = max(snapshot.captured_at for snapshot in snapshots)
    if fixture_now.tzinfo is None or fixture_now.utcoffset() is None:
        raise RuntimeError("固定数据源的行情快照时间必须包含时区")
    fixture_date = fixture_now.astimezone(SHANGHAI).date()
    await _run_blocking_safely(repository.upsert_stocks, stocks)
    await _run_blocking_safely(
        repository.commit_snapshot_success,
        snapshots,
        started_at=fixture_now,
        source=snapshots[0].source,
        market_time=fixture_now,
        expected_row_count=len(snapshots),
        quality_status=(
            "ok"
            if all(snapshot.quality_status.value == "ok" for snapshot in snapshots)
            else "partial"
        ),
    )

    trading_days = await source.fetch_trading_days(
        fixture_date - timedelta(days=370), fixture_date
    )
    for stock in stocks:
        daily_bars = await source.fetch_daily_bars(
            stock.code,
            fixture_date - timedelta(days=370),
            fixture_date,
            "1d",
            "qfq",
        )
        minute_bars = await source.fetch_minute_bars(
            stock.code,
            datetime.combine(fixture_date, time(9, 30), tzinfo=SHANGHAI),
            datetime.combine(fixture_date, time(15, 0), tzinfo=SHANGHAI),
            "1m",
            "none",
        )
        await _run_blocking_safely(
            repository.upsert_bars,
            [*_batch_bars(daily_bars), *_batch_bars(minute_bars)],
        )
    return trading_days, fixture_now


def create_app(
    settings: Settings | None = None,
    source: MarketDataSource | None = None,
    database: Database | None = None,
    now_provider: Callable[[], datetime] | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_source = source or (
        FixtureSource() if resolved_settings.fixture_source else AkshareSource()
    )
    fixture_now = getattr(resolved_source, "captured_at", None)

    def system_now() -> datetime:
        return datetime.now(SHANGHAI)

    fixture_clock = [fixture_now if isinstance(fixture_now, datetime) else None]
    if resolved_settings.fixture_source:

        def resolved_now_provider() -> datetime:
            return fixture_clock[0] or (now_provider or system_now)()

    else:
        resolved_now_provider = now_provider or system_now

    injected_repository = MarketRepository(database) if database is not None else None
    injected_bar_service = (
        BarService(resolved_source, injected_repository, resolved_settings.history_days)
        if injected_repository is not None
        else None
    )
    injected_indicator_service = (
        IndicatorService(injected_repository) if injected_repository is not None else None
    )
    stock_master_minimum_count = (
        1
        if source is not None or resolved_settings.fixture_source
        else DEFAULT_STOCK_MASTER_MINIMUM_COUNT
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_database = database or Database(resolved_settings.database_path)
        scheduler = None
        initial_snapshot_task = None
        history_task = None
        indicator_task = None
        bar_service = injected_bar_service
        indicator_service = injected_indicator_service
        try:
            repository = injected_repository or MarketRepository(resolved_database)
            bar_service = bar_service or BarService(
                resolved_source, repository, resolved_settings.history_days
            )
            indicator_service = indicator_service or IndicatorService(repository)
            app.state.repository = repository
            app.state.bar_service = bar_service
            app.state.indicator_service = indicator_service

            if resolved_settings.fixture_source:
                trading_days, persisted_fixture_now = await _persist_fixture_data(
                    resolved_source, repository
                )
                fixture_clock[0] = persisted_fixture_now
                app.state.clock = MarketClock(trading_days)
                yield
                return

            try:
                stocks = await resolved_source.fetch_stock_master()
                stocks = _validated_stock_master(stocks, stock_master_minimum_count)
            except Exception:
                logger.exception("加载股票主数据失败，继续使用本地已有数据")
            else:
                await _run_blocking_safely(repository.upsert_stocks, stocks)

            bootstrapper = HistoryBootstrapper(
                bar_service,
                repository,
                resolved_settings.history_request_delay_seconds,
                now_provider=resolved_now_provider,
            )

            today = resolved_now_provider().date()
            cached_trading_days = await _run_blocking_safely(
                repository.list_trading_days
            )
            try:
                trading_days = await resolved_source.fetch_trading_days(
                    today - timedelta(days=370), today + timedelta(days=370)
                )
                if not trading_days:
                    raise RuntimeError("上游交易日历为空")
            except Exception:
                logger.exception("加载交易日失败，继续使用本地交易日历")
                trading_days = cached_trading_days
            else:
                await _run_blocking_safely(
                    repository.replace_trading_days, trading_days
                )

            clock = MarketClock(trading_days)
            minimum_expected_count = (
                1 if source is not None or resolved_settings.fixture_source else 4000
            )
            collector = SnapshotCollector(
                resolved_source,
                repository,
                minimum_expected_count=minimum_expected_count,
                learn_unknown_stocks=not resolved_settings.fixture_source,
            )
            app.state.clock = clock
            app.state.collector = collector

            async def refresh_macd_later(at: datetime) -> None:
                try:
                    await indicator_service.refresh_market_macd(
                        at,
                        market_open=clock.is_open(at),
                    )
                except Exception:
                    logger.exception("MACD 指标刷新失败，继续保留上一批有效指标")

            now = resolved_now_provider()
            data_status = await _run_blocking_safely(repository.data_status)
            expected_quote_count = max(
                minimum_expected_count, data_status.stock_count
            )

            async def collect_initial_snapshot_later(snapshot_at: datetime) -> None:
                try:
                    await collector.collect_once(snapshot_at)
                    await refresh_macd_later(snapshot_at)
                except Exception:
                    logger.exception("首次全市场行情采集失败，继续使用本地已有数据")

            if clock.is_open(now) or (
                clock.trading_days
                and data_status.latest_quote_count < expected_quote_count
            ):
                initial_snapshot_task = asyncio.create_task(
                    collect_initial_snapshot_later(now)
                )
                app.state.initial_snapshot_task = initial_snapshot_task
            else:
                indicator_task = asyncio.create_task(refresh_macd_later(now))
                app.state.indicator_task = indicator_task

            async def bootstrap_history_later() -> None:
                await bootstrapper.run()
                await refresh_macd_later(resolved_now_provider())

            history_task = asyncio.create_task(bootstrap_history_later())
            app.state.history_task = history_task

            async def archive_at(at: datetime) -> None:
                failures = await _run_blocking_safely(
                    archive_pending_snapshots,
                    repository,
                    resolved_settings.data_dir,
                    at,
                )
                for trade_date, error in failures.items():
                    logger.error("归档 %s 失败，保留热数据等待重试：%s", trade_date, error)

            await archive_at(resolved_now_provider())

            async def archive_later() -> None:
                await archive_at(resolved_now_provider())

            async def maintenance_later() -> None:
                now = resolved_now_provider()
                try:
                    refreshed_stocks = await resolved_source.fetch_stock_master()
                    refreshed_stocks = _validated_stock_master(
                        refreshed_stocks, stock_master_minimum_count
                    )
                except Exception:
                    logger.exception("刷新股票主数据失败，继续使用本地已有数据")
                else:
                    await _run_blocking_safely(
                        repository.upsert_stocks, refreshed_stocks
                    )

                try:
                    refreshed_days = await resolved_source.fetch_trading_days(
                        now.date() - timedelta(days=370),
                        now.date() + timedelta(days=370),
                    )
                    if not refreshed_days:
                        raise RuntimeError("上游交易日历为空")
                except Exception:
                    logger.exception("刷新交易日历失败，继续使用最近有效值")
                else:
                    await _run_blocking_safely(
                        repository.replace_trading_days, refreshed_days
                    )
                    clock.replace_trading_days(refreshed_days)

                await archive_at(now)

            async def daily_history_later() -> None:
                await bootstrapper.run()
                await refresh_macd_later(resolved_now_provider())

            scheduler = create_scheduler(
                clock,
                collector,
                archive_later,
                maintenance_later,
                daily_history_later,
                refresh_macd_later,
            )
            app.state.scheduler = scheduler
            scheduler.start()
            yield
        finally:
            try:
                if initial_snapshot_task is not None:
                    initial_snapshot_task.cancel()
                    try:
                        await initial_snapshot_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("首次全市场行情采集任务异常退出")
            finally:
                try:
                    if indicator_task is not None:
                        indicator_task.cancel()
                        try:
                            await indicator_task
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            logger.exception("MACD 预热任务异常退出")
                finally:
                    try:
                        if history_task is not None:
                            history_task.cancel()
                            try:
                                await history_task
                            except asyncio.CancelledError:
                                pass
                            except Exception:
                                logger.exception("日 K 回补任务异常退出")
                    finally:
                        try:
                            if scheduler is not None and scheduler.running:
                                await shutdown_scheduler(scheduler)
                        finally:
                            if database is None:
                                try:
                                    if bar_service is not None:
                                        await bar_service.close()
                                finally:
                                    resolved_database.close()
                            elif bar_service is not None:
                                await bar_service.close()

    app = FastAPI(title="A 股雷达", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            f"http://127.0.0.1:{resolved_settings.frontend_port}",
            f"http://localhost:{resolved_settings.frontend_port}",
        ],
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )
    app.state.settings = resolved_settings
    app.state.repository = injected_repository
    app.state.bar_service = injected_bar_service
    app.state.source = resolved_source
    app.state.clock = None
    app.state.scheduler = None
    app.state.initial_snapshot_task = None
    app.state.history_task = None
    app.state.indicator_task = None
    app.state.indicator_service = injected_indicator_service
    app.state.now_provider = resolved_now_provider

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "location": list(error["loc"]),
                "message": (
                    "缺少必填参数" if error["type"] == "missing" else "参数值不合法"
                ),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": "请求参数校验失败", "errors": errors},
        )

    @app.exception_handler(Exception)
    async def internal_server_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("HTTP 请求处理失败", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "系统内部错误"})

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
