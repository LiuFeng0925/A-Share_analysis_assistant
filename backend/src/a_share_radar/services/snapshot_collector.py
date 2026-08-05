import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any

from a_share_radar.data_sources.protocol import MarketDataSource
from a_share_radar.domain.models import Stock
from a_share_radar.storage.repository import MarketRepository


class SnapshotValidationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CollectionResult:
    captured_at: datetime
    row_count: int
    source: str
    expected_row_count: int
    coverage_ratio: float
    quality_status: str


class SnapshotCollector:
    def __init__(
        self,
        source: MarketDataSource,
        repository: MarketRepository,
        minimum_expected_count: int = 4000,
        minimum_coverage_ratio: float = 0.9,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
        maximum_market_time_skew: timedelta = timedelta(minutes=10),
        learn_unknown_stocks: bool = False,
    ):
        self.source = source
        self.repository = repository
        self.minimum_expected_count = minimum_expected_count
        self.minimum_coverage_ratio = minimum_coverage_ratio
        self.retry_delays = retry_delays
        self.maximum_market_time_skew = maximum_market_time_skew
        self.learn_unknown_stocks = learn_unknown_stocks

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
        started_at = at or datetime.now(UTC)
        expected_count = self.minimum_expected_count
        source_name = type(self.source).__name__
        quotes = []
        market_time = None
        stage = "preflight"
        try:
            expected_count = await self._run_blocking(
                self.repository.snapshot_expectation, self.minimum_expected_count
            )
            stage = "fetch"
            quotes = await self._fetch_quotes()
            source_name = quotes[0].source if quotes else source_name
            market_time = self._candidate_market_time(quotes)
            stage = "validate"
            market_time, quality_status = await self._validate_batch(
                quotes, started_at, expected_count
            )
            stage = "commit_success"
            await self._run_blocking(
                self.repository.commit_snapshot_success,
                quotes,
                started_at=started_at,
                source=source_name,
                market_time=market_time,
                expected_row_count=expected_count,
                quality_status=quality_status,
            )
        except asyncio.CancelledError:
            if stage == "commit_success":
                raise
            await self._record_failure(
                started_at,
                source_name,
                market_time,
                expected_count,
                len(quotes),
                "行情采集任务已取消",
            )
            raise
        except Exception as exc:
            await self._record_failure(
                started_at,
                source_name,
                market_time,
                expected_count,
                len(quotes),
                self._safe_error_message(stage, exc),
            )
            raise

        return CollectionResult(
            market_time,
            len(quotes),
            source_name,
            expected_count,
            len(quotes) / expected_count,
            quality_status,
        )

    async def _validate_batch(
        self, quotes: list, at: datetime, expected_count: int
    ) -> tuple[datetime, str]:
        if not quotes:
            raise SnapshotValidationError("行情数量异常：未取得任何行情")
        identities = {(quote.market, quote.code) for quote in quotes}
        if len(quotes) < self.minimum_expected_count:
            raise SnapshotValidationError(f"行情数量异常：仅取得 {len(quotes)} 条")
        coverage_ratio = len(quotes) / expected_count
        if coverage_ratio < self.minimum_coverage_ratio:
            raise SnapshotValidationError(
                f"行情覆盖率异常：实际 {len(quotes)} 条，预期 {expected_count} 条"
            )
        if len(identities) != len(quotes):
            raise SnapshotValidationError("行情批次包含重复股票代码")
        known_identities = await self._run_blocking(self.repository.stock_identities)
        unknown = identities - known_identities
        if at.tzinfo is None or at.utcoffset() is None:
            raise SnapshotValidationError("采集触发时间必须包含时区信息")

        normalized_times = set()
        quality_status = "ok"
        critical_fields = (
            "latest_price",
            "open_price",
            "high_price",
            "low_price",
            "previous_close",
            "volume",
            "amount",
        )
        nonnegative_fields = (*critical_fields, "turnover_rate", "total_market_cap")
        finite_fields = (*nonnegative_fields, "change_percent", "change_amount")
        for quote in quotes:
            captured_at = quote.captured_at
            if captured_at.tzinfo is None or captured_at.utcoffset() is None:
                raise SnapshotValidationError("行情市场时间必须包含时区信息")
            normalized_times.add(captured_at.astimezone(UTC))
            for field in finite_fields:
                value = getattr(quote, field)
                if value is not None and not isfinite(float(value)):
                    raise SnapshotValidationError(f"行情字段 {field} 必须是有限数值")
            for field in nonnegative_fields:
                value = getattr(quote, field)
                if value is not None and value < 0:
                    raise SnapshotValidationError(f"行情字段 {field} 必须非负")
            if not self._ohlc_is_valid(quote):
                raise SnapshotValidationError("行情 OHLC 关系异常")
            if quote.quality_status.value != "ok" or any(
                getattr(quote, field) is None for field in critical_fields
            ):
                quality_status = "partial"

        if len(normalized_times) != 1:
            raise SnapshotValidationError("行情批次市场时间不统一")
        market_time = normalized_times.pop()
        if abs(market_time - at.astimezone(UTC)) > self.maximum_market_time_skew:
            raise SnapshotValidationError("行情市场时间异常，偏离采集时间过大")
        if unknown and self.learn_unknown_stocks:
            await self._run_blocking(
                self.repository.upsert_stocks,
                sorted(
                    (
                        Stock(quote.code, quote.market, quote.name)
                        for quote in quotes
                        if (quote.market, quote.code) in unknown
                    ),
                    key=lambda stock: (stock.market.value, stock.code),
                ),
            )
            known_identities = await self._run_blocking(
                self.repository.stock_identities
            )
            unknown = identities - known_identities
        if unknown:
            raise SnapshotValidationError("行情批次包含未知股票代码或市场")
        return market_time, quality_status

    @staticmethod
    def _ohlc_is_valid(quote: Any) -> bool:
        if quote.high_price is not None and quote.low_price is not None:
            if quote.high_price < quote.low_price:
                return False
            for value in (quote.open_price, quote.latest_price):
                if value is not None and not quote.low_price <= value <= quote.high_price:
                    return False
        return True

    @staticmethod
    def _candidate_market_time(quotes: list) -> datetime | None:
        normalized_times = {
            quote.captured_at.astimezone(UTC)
            for quote in quotes
            if quote.captured_at.tzinfo is not None
            and quote.captured_at.utcoffset() is not None
        }
        if len(normalized_times) == 1:
            return normalized_times.pop()
        return None

    async def _record_failure(
        self,
        started_at: datetime,
        source: str,
        market_time: datetime | None,
        expected_count: int,
        actual_count: int,
        error_message: str,
    ) -> None:
        await self._run_blocking(
            self.repository.record_ingestion_run,
            kind="snapshot",
            started_at=started_at,
            finished_at=datetime.now(UTC),
            source=source,
            market_time=market_time,
            expected_row_count=expected_count,
            actual_row_count=actual_count,
            status="failed",
            quality_status="error",
            error_message=error_message,
        )

    @staticmethod
    def _safe_error_message(stage: str, exc: Exception) -> str:
        if isinstance(exc, SnapshotValidationError):
            return str(exc)
        if stage == "preflight":
            return "行情采集预检失败"
        if stage == "fetch":
            return "上游行情获取失败"
        if stage == "commit_success":
            return "行情存储失败"
        return "行情采集失败"

    @staticmethod
    async def _run_blocking(function, *args, **kwargs):
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise
