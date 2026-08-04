"""为 Playwright 启动使用独立临时数据库的固定数据后端。"""

import os
import signal
import subprocess
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def run(port: int = 18000) -> int:
    with tempfile.TemporaryDirectory(prefix="a-share-radar-e2e-") as data_dir:
        environment = os.environ.copy()
        environment.update(
            {
                "A_SHARE_FIXTURE_SOURCE": "true",
                "A_SHARE_DATA_DIR": data_dir,
                "A_SHARE_FRONTEND_PORT": "4173",
            }
        )
        command = [
            str(PROJECT_DIR / "backend" / ".venv" / "bin" / "uvicorn"),
            "a_share_radar.main:app",
            "--app-dir",
            str(PROJECT_DIR / "backend" / "src"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]
        process = subprocess.Popen(command, env=environment)
        previous_handlers: dict[signal.Signals, object] = {}

        def stop_child(_signum, _frame) -> None:
            if process.poll() is None:
                process.terminate()

        for signal_name in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signal_name] = signal.getsignal(signal_name)
            signal.signal(signal_name, stop_child)
        try:
            return process.wait()
        finally:
            for signal_name, handler in previous_handlers.items():
                signal.signal(signal_name, handler)
            if process.poll() is None:
                process.terminate()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(run())
