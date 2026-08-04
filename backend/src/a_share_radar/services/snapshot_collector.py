import asyncio
from dataclasses import dataclass
from datetime import datetime

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.storage.repository import MarketRepository


class SnapshotValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CollectionResult:
    captured_at: datetime
    row_count: int
    source: str


class SnapshotCollector:
    def __init__(
        self,
        source: MarketDataSource,
        repository: MarketRepository,
        minimum_expected_count: int = 4000,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
    ):
        self.source = source
        self.repository = repository
        self.minimum_expected_count = minimum_expected_count
        self.retry_delays = retry_delays

    async def _fetch_quotes(self):
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return await self.source.fetch_market_snapshot()
            except Exception:
                if attempt == len(self.retry_delays):
                    raise
                await asyncio.sleep(self.retry_delays[attempt])
        raise AssertionError("重试循环不应运行到此处")

    async def collect_once(self, at: datetime | None = None) -> CollectionResult:
        quotes = await self._fetch_quotes()
        identities = {(quote.market, quote.code) for quote in quotes}
        if len(quotes) < self.minimum_expected_count:
            raise SnapshotValidationError(f"行情数量异常：仅取得 {len(quotes)} 条")
        if len(identities) != len(quotes):
            raise SnapshotValidationError("行情批次包含重复股票代码")
        self.repository.save_snapshot(quotes)
        return CollectionResult(quotes[0].captured_at, len(quotes), quotes[0].source)
