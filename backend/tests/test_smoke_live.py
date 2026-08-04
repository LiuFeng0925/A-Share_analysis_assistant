import importlib.util
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = PROJECT_DIR / "scripts" / "smoke_live.py"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("smoke_live", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_script_can_run_from_any_working_directory(tmp_path):
    environment = os.environ.copy()
    environment.pop("A_SHARE_RUN_LIVE", None)

    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "A_SHARE_RUN_LIVE=1" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


async def test_live_smoke_requires_explicit_environment_permission(monkeypatch):
    monkeypatch.delenv("A_SHARE_RUN_LIVE", raising=False)
    module = load_smoke_module()

    with pytest.raises(SystemExit, match="A_SHARE_RUN_LIVE=1"):
        await module.run()


async def test_live_smoke_calls_snapshot_once_and_validates_market(monkeypatch, capsys):
    monkeypatch.setenv("A_SHARE_RUN_LIVE", "1")
    module = load_smoke_module()
    captured_at = datetime(2026, 8, 4, 10, 30, tzinfo=SHANGHAI)

    class RecordingSource:
        def __init__(self):
            self.calls = 0

        async def fetch_market_snapshot(self):
            self.calls += 1
            codes = [f"{index:06d}" for index in range(4001)]
            codes[519] = "600519"
            rows = [
                SimpleNamespace(code=code, captured_at=captured_at) for code in codes
            ]
            rows[519] = SimpleNamespace(
                code="600519",
                market="SH",
                name="贵州茅台",
                captured_at=captured_at,
                latest_price=1588.88,
                change_percent=2.36,
                change_amount=36.56,
                open_price=1558.20,
                high_price=1599.90,
                low_price=1551.01,
                previous_close=1552.32,
                volume=3_821_100,
                amount=6_058_000_000.0,
                turnover_rate=0.30,
                total_market_cap=1_995_000_000_000.0,
                quality_status="ok",
            )
            return rows

    source = RecordingSource()
    await module.run(source)

    assert source.calls == 1
    output = capsys.readouterr().out
    assert "全市场股票数量：4001" in output
    assert "获取时间：2026-08-04 10:30:00" in output
    assert "代表股票校验：600519 贵州茅台" in output
    assert "成交量口径：股" in output
    assert "在线行情接口可用" in output


async def test_live_smoke_rejects_truncated_market(monkeypatch):
    monkeypatch.setenv("A_SHARE_RUN_LIVE", "1")
    module = load_smoke_module()

    class TruncatedSource:
        async def fetch_market_snapshot(self):
            return [SimpleNamespace(code="600519", captured_at=None)]

    with pytest.raises(RuntimeError, match="数量异常"):
        await module.run(TruncatedSource())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"open_price": 0.0}, "OHLC"),
        ({"high_price": 1500.0}, "OHLC"),
        ({"volume": -100}, "成交量"),
        ({"volume": 38_211}, "股口径"),
        ({"amount": -1.0}, "成交额"),
        ({"captured_at": datetime(2026, 8, 4, 10, 30)}, "时区"),  # noqa: DTZ001
    ],
)
async def test_live_smoke_rejects_invalid_representative_contract(
    monkeypatch, changes, message
):
    monkeypatch.setenv("A_SHARE_RUN_LIVE", "1")
    module = load_smoke_module()
    captured_at = datetime(2026, 8, 4, 10, 30, tzinfo=SHANGHAI)
    representative = {
        "code": "600519",
        "market": "SH",
        "name": "贵州茅台",
        "captured_at": captured_at,
        "latest_price": 1588.88,
        "change_percent": 2.36,
        "change_amount": 36.56,
        "open_price": 1558.20,
        "high_price": 1599.90,
        "low_price": 1551.01,
        "previous_close": 1552.32,
        "volume": 3_821_100,
        "amount": 6_058_000_000.0,
        "turnover_rate": 0.30,
        "total_market_cap": 1_995_000_000_000.0,
        "quality_status": "ok",
    }
    representative.update(changes)

    class InvalidSource:
        async def fetch_market_snapshot(self):
            rows = [
                SimpleNamespace(code=f"{index:06d}", captured_at=captured_at)
                for index in range(4001)
            ]
            rows[519] = SimpleNamespace(**representative)
            return rows

    with pytest.raises(RuntimeError, match=message):
        await module.run(InvalidSource())
