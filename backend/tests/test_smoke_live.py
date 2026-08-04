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
            return [
                SimpleNamespace(code=code, captured_at=captured_at) for code in codes
            ]

    source = RecordingSource()
    await module.run(source)

    assert source.calls == 1
    output = capsys.readouterr().out
    assert "全市场股票数量：4001" in output
    assert "获取时间：2026-08-04 10:30:00" in output
    assert "在线行情接口可用" in output


async def test_live_smoke_rejects_truncated_market(monkeypatch):
    monkeypatch.setenv("A_SHARE_RUN_LIVE", "1")
    module = load_smoke_module()

    class TruncatedSource:
        async def fetch_market_snapshot(self):
            return [SimpleNamespace(code="600519", captured_at=None)]

    with pytest.raises(RuntimeError, match="数量异常"):
        await module.run(TruncatedSource())
