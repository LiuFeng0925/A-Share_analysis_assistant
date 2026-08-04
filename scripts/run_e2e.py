"""在父进程中管理 Playwright 进程组与独立数据目录的完整生命周期。"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_DIR / "frontend"
TEMP_PREFIX = "a-share-radar-e2e-"
POLL_SECONDS = 0.05
DEFAULT_SIGNAL_GRACE_SECONDS = 5.0
DEFAULT_TERMINATE_GRACE_SECONDS = 2.0


def default_playwright_command() -> list[str]:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("未找到 pnpm，无法启动 Playwright")
    return [pnpm, "exec", "playwright", "test"]


def _start_process(
    command: Sequence[str], environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(
        list(command),
        cwd=FRONTEND_DIR,
        env=environment,
        **options,
    )


def _send_group_signal(process: subprocess.Popen[bytes], received_signal: int) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            if received_signal == signal.SIGINT and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
        else:
            os.killpg(process.pid, received_signal)
    except ProcessLookupError:
        return


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    _send_group_signal(process, signal.SIGTERM)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _wait_after_signal(
    process: subprocess.Popen[bytes],
    *,
    signal_grace_seconds: float,
    terminate_grace_seconds: float,
) -> None:
    try:
        process.wait(timeout=signal_grace_seconds)
        return
    except subprocess.TimeoutExpired:
        _terminate_group(process)
    try:
        process.wait(timeout=terminate_grace_seconds)
        return
    except subprocess.TimeoutExpired:
        _kill_group(process)
    process.wait(timeout=terminate_grace_seconds)


def _ensure_stopped(
    process: subprocess.Popen[bytes], terminate_grace_seconds: float
) -> None:
    if process.poll() is not None:
        return
    _terminate_group(process)
    try:
        process.wait(timeout=terminate_grace_seconds)
    except subprocess.TimeoutExpired:
        _kill_group(process)
        process.wait(timeout=terminate_grace_seconds)


def run(
    *,
    temp_root: Path | None = None,
    command: Sequence[str] | None = None,
    signal_grace_seconds: float = DEFAULT_SIGNAL_GRACE_SECONDS,
    terminate_grace_seconds: float = DEFAULT_TERMINATE_GRACE_SECONDS,
) -> int:
    process: subprocess.Popen[bytes] | None = None
    data_dir: Path | None = None
    previous_handlers: dict[int, signal.Handlers] = {}
    received_signals: list[int] = []

    def forward_signal(received_signal: int, _frame) -> None:
        if not received_signals:
            received_signals.append(received_signal)
        if process is not None:
            _send_group_signal(process, received_signal)

    for managed_signal in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[managed_signal] = signal.getsignal(managed_signal)
        signal.signal(managed_signal, forward_signal)

    try:
        resolved_command = list(command) if command is not None else default_playwright_command()
        if received_signals:
            return 128 + received_signals[0]
        data_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=temp_root))
        environment = os.environ.copy()
        environment["A_SHARE_E2E_DATA_DIR"] = str(data_dir)
        environment["A_SHARE_E2E_PYTHON"] = sys.executable
        print(f"E2E 临时目录：{data_dir}", flush=True)
        if received_signals:
            return 128 + received_signals[0]
        process = _start_process(resolved_command, environment)
        if received_signals:
            _send_group_signal(process, received_signals[0])

        while True:
            try:
                return_code = process.wait(timeout=POLL_SECONDS)
                if received_signals:
                    return 128 + received_signals[0]
                return return_code
            except subprocess.TimeoutExpired:
                if not received_signals:
                    continue
                _wait_after_signal(
                    process,
                    signal_grace_seconds=signal_grace_seconds,
                    terminate_grace_seconds=terminate_grace_seconds,
                )
                return 128 + received_signals[0]
    finally:
        for managed_signal, previous_handler in previous_handlers.items():
            signal.signal(managed_signal, previous_handler)
        if process is not None:
            _ensure_stopped(process, terminate_grace_seconds)
        if data_dir is not None:
            shutil.rmtree(data_dir)
            print(f"E2E 临时目录已清理：{data_dir}", flush=True)


if __name__ == "__main__":
    raise SystemExit(run())
