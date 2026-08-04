import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from a_share_radar.config import Settings
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.scheduler import create_scheduler, shutdown_scheduler
from a_share_radar.services.snapshot_collector import SnapshotCollector
from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def create_app(
    settings: Settings | None = None,
    source: MarketDataSource | None = None,
    database: Database | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_source = source or AkshareSource()
    injected_repository = MarketRepository(database) if database is not None else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_database = database or Database(resolved_settings.database_path)
        scheduler = None
        try:
            repository = injected_repository or MarketRepository(resolved_database)
            app.state.repository = repository

            try:
                stocks = await resolved_source.fetch_stock_master()
            except Exception:
                logger.exception("加载股票主数据失败，继续使用本地已有数据")
            else:
                repository.upsert_stocks(stocks)

            today = datetime.now(SHANGHAI).date()
            try:
                trading_days = await resolved_source.fetch_trading_days(
                    today - timedelta(days=370), today + timedelta(days=370)
                )
            except Exception:
                logger.exception("加载交易日失败，本轮按闭市处理")
                trading_days = set()

            clock = MarketClock(trading_days)
            minimum_expected_count = (
                1 if source is not None or resolved_settings.fixture_source else 4000
            )
            collector = SnapshotCollector(
                resolved_source,
                repository,
                minimum_expected_count=minimum_expected_count,
            )
            app.state.clock = clock
            app.state.collector = collector

            now = datetime.now(SHANGHAI)
            if clock.is_open(now):
                try:
                    await collector.collect_once(now)
                except Exception:
                    logger.exception("首次全市场行情采集失败，继续使用本地已有数据")

            async def archive_later() -> None:
                return None

            scheduler = create_scheduler(clock, collector, archive_later)
            app.state.scheduler = scheduler
            scheduler.start()
            yield
        finally:
            try:
                if scheduler is not None and scheduler.running:
                    await shutdown_scheduler(scheduler)
            finally:
                if database is None:
                    resolved_database.close()

    app = FastAPI(title="A 股雷达", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.repository = injected_repository
    app.state.source = resolved_source
    app.state.scheduler = None

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
