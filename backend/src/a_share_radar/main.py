from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI

from a_share_radar.config import Settings
from a_share_radar.data_sources.akshare_source import AkshareSource
from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.services.market_clock import MarketClock
from a_share_radar.services.scheduler import create_scheduler
from a_share_radar.services.snapshot_collector import SnapshotCollector
from a_share_radar.storage.database import Database
from a_share_radar.storage.repository import MarketRepository


def create_app(
    settings: Settings | None = None,
    source: MarketDataSource | None = None,
    database: Database | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_database = database or Database(resolved_settings.database_path)
    resolved_source = source or AkshareSource()
    repository = MarketRepository(resolved_database)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stocks = await resolved_source.fetch_stock_master()
        repository.upsert_stocks(stocks)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        trading_days = await resolved_source.fetch_trading_days(
            today - timedelta(days=370), today + timedelta(days=370)
        )
        clock = MarketClock(trading_days)
        minimum_expected_count = 1 if source is not None or resolved_settings.fixture_source else 4000
        collector = SnapshotCollector(
            resolved_source,
            repository,
            minimum_expected_count=minimum_expected_count,
        )
        await collector.collect_once(datetime.now(ZoneInfo("Asia/Shanghai")))

        async def archive_later() -> None:
            return None

        scheduler = create_scheduler(clock, collector, archive_later)
        app.state.repository = repository
        app.state.source = resolved_source
        app.state.clock = clock
        app.state.collector = collector
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            if database is None:
                resolved_database.close()

    app = FastAPI(title="A 股雷达", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.repository = repository
    app.state.source = resolved_source

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
