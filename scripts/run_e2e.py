"""由单一父进程管理 E2E 服务树、Playwright 与临时数据目录。"""

import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
TEMP_PREFIX = "a-share-radar-e2e-"
UVICORN_PORT = 18000
VITE_PORT = 4173
POLL_SECONDS = 0.05
READY_TIMEOUT_SECONDS = 15.0
SIGNAL_GRACE_SECONDS = 5.0
TERMINATE_GRACE_SECONDS = 2.0
PORT_RELEASE_TIMEOUT_SECONDS = 5.0


@dataclass
class ShutdownState:
    first_signal: int | None = None
    force_requested: bool = False


class ShutdownRequested(Exception):
    """标记受控信号退出，实际退出码在完整清理后决定。"""


def _resolve_pnpm() -> str:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        raise RuntimeError("未找到 pnpm，无法启动 E2E")
    return pnpm


def default_uvicorn_command() -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "a_share_radar.main:app",
        "--app-dir",
        str(BACKEND_DIR / "src"),
        "--host",
        "127.0.0.1",
        "--port",
        str(UVICORN_PORT),
    ]


def default_vite_command() -> list[str]:
    return [
        _resolve_pnpm(),
        "exec",
        "vite",
        "--host",
        "127.0.0.1",
        "--port",
        str(VITE_PORT),
    ]


def default_playwright_command() -> list[str]:
    return [_resolve_pnpm(), "exec", "playwright", "test"]


def _start_process(
    command: Sequence[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    options: dict[str, object] = {}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        **options,
    )


def _start_owned_process(
    label: str,
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> subprocess.Popen[bytes]:
    process = _start_process(command, cwd=cwd, environment=environment)
    print(f"E2E {label} PID：{process.pid}", flush=True)
    return process


def _taskkill(process_id: int, *, force: bool) -> None:
    command = ["taskkill", "/PID", str(process_id), "/T"]
    if force:
        command.append("/F")
    subprocess.run(command, check=False, capture_output=True, timeout=5)


def _send_tree_signal(process: subprocess.Popen[bytes], sent_signal: int) -> None:
    try:
        if os.name == "nt":
            if process.poll() is not None:
                return
            if sent_signal == signal.SIGINT and hasattr(signal, "CTRL_BREAK_EVENT"):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                _taskkill(process.pid, force=False)
        else:
            os.killpg(process.pid, sent_signal)
    except ProcessLookupError:
        return


def _kill_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name == "nt":
            if process.poll() is not None:
                return
            _taskkill(process.pid, force=True)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _tree_exists(process: subprocess.Popen[bytes]) -> bool:
    process.poll()
    if os.name == "nt":
        return process.returncode is None
    try:
        os.killpg(process.pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_for_trees(
    processes: Sequence[subprocess.Popen[bytes]],
    *,
    timeout: float,
    shutdown_state: ShutdownState,
) -> list[subprocess.Popen[bytes]]:
    deadline = time.monotonic() + timeout
    alive = [process for process in processes if _tree_exists(process)]
    while alive and time.monotonic() < deadline and not shutdown_state.force_requested:
        time.sleep(POLL_SECONDS)
        alive = [process for process in alive if _tree_exists(process)]
    return alive


def _stop_processes(
    processes: Sequence[subprocess.Popen[bytes]],
    *,
    sent_signal: int,
    shutdown_state: ShutdownState,
) -> None:
    alive = [process for process in processes if _tree_exists(process)]
    for process in alive:
        _send_tree_signal(process, sent_signal)
    alive = _wait_for_trees(
        alive,
        timeout=SIGNAL_GRACE_SECONDS,
        shutdown_state=shutdown_state,
    )
    if alive:
        for process in alive:
            _kill_tree(process)
        alive = _wait_for_trees(
            alive,
            timeout=TERMINATE_GRACE_SECONDS,
            shutdown_state=ShutdownState(),
        )
    if alive:
        raise RuntimeError("E2E 进程树未能在限定时间内退出")
    for process in processes:
        process.poll()


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def _uvicorn_is_healthy() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{UVICORN_PORT}/api/health", timeout=0.2
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_until_ready(
    label: str,
    process: subprocess.Popen[bytes],
    probe: Callable[[], bool],
    shutdown_state: ShutdownState,
) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if shutdown_state.first_signal is not None:
            return False
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"E2E {label} 在就绪前退出：{return_code}")
        if probe():
            return True
        time.sleep(POLL_SECONDS)
    raise RuntimeError(f"等待 E2E {label} 就绪超时")


def _wait_until_ports_released() -> None:
    deadline = time.monotonic() + PORT_RELEASE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not any(_port_is_listening(port) for port in (UVICORN_PORT, VITE_PORT)):
            return
        time.sleep(POLL_SECONDS)
    raise RuntimeError("E2E 服务端口未能在限定时间内释放")


def _ensure_ports_are_free() -> None:
    occupied = [
        str(port) for port in (UVICORN_PORT, VITE_PORT) if _port_is_listening(port)
    ]
    if occupied:
        raise RuntimeError(f"E2E 启动前端口已被占用：{', '.join(occupied)}")


def run(
    *,
    temp_root: Path | None = None,
    playwright_command: Sequence[str] | None = None,
) -> int:
    shutdown_state = ShutdownState()
    result_code = 1
    uvicorn_process: subprocess.Popen[bytes] | None = None
    vite_process: subprocess.Popen[bytes] | None = None
    playwright_process: subprocess.Popen[bytes] | None = None
    data_dir: Path | None = None
    previous_handlers: dict[int, signal.Handlers] = {}

    def record_signal(received_signal: int, _frame) -> None:
        if shutdown_state.first_signal is None:
            shutdown_state.first_signal = received_signal
        else:
            shutdown_state.force_requested = True

    for managed_signal in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[managed_signal] = signal.getsignal(managed_signal)
        signal.signal(managed_signal, record_signal)

    print(f"E2E runner PID：{os.getpid()}", flush=True)
    try:
        uvicorn_command = default_uvicorn_command()
        vite_command = default_vite_command()
        resolved_playwright = (
            list(playwright_command)
            if playwright_command is not None
            else default_playwright_command()
        )
        if shutdown_state.first_signal is not None:
            raise ShutdownRequested
        _ensure_ports_are_free()
        data_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=temp_root))
        print(f"E2E 临时目录：{data_dir}", flush=True)

        backend_environment = os.environ.copy()
        backend_environment.update(
            {
                "A_SHARE_FIXTURE_SOURCE": "true",
                "A_SHARE_DATA_DIR": str(data_dir),
                "A_SHARE_FRONTEND_PORT": str(VITE_PORT),
            }
        )
        frontend_environment = os.environ.copy()
        frontend_environment["VITE_API_BASE_URL"] = (
            f"http://127.0.0.1:{UVICORN_PORT}"
        )
        playwright_environment = os.environ.copy()
        playwright_environment["A_SHARE_E2E_DATA_DIR"] = str(data_dir)

        uvicorn_process = _start_owned_process(
            "Uvicorn",
            uvicorn_command,
            cwd=PROJECT_DIR,
            environment=backend_environment,
        )
        if not _wait_until_ready(
            "Uvicorn", uvicorn_process, _uvicorn_is_healthy, shutdown_state
        ):
            raise ShutdownRequested

        vite_process = _start_owned_process(
            "Vite",
            vite_command,
            cwd=FRONTEND_DIR,
            environment=frontend_environment,
        )
        if not _wait_until_ready(
            "Vite",
            vite_process,
            lambda: _port_is_listening(VITE_PORT),
            shutdown_state,
        ):
            raise ShutdownRequested

        playwright_process = _start_owned_process(
            "Playwright",
            resolved_playwright,
            cwd=FRONTEND_DIR,
            environment=playwright_environment,
        )
        while playwright_process.poll() is None:
            if shutdown_state.first_signal is not None:
                raise ShutdownRequested
            if uvicorn_process.poll() is not None or vite_process.poll() is not None:
                raise RuntimeError("E2E 服务在 Playwright 运行期间意外退出")
            time.sleep(POLL_SECONDS)
        assert playwright_process.returncode is not None, "Playwright 应已退出"
        result_code = playwright_process.returncode
    except ShutdownRequested:
        assert shutdown_state.first_signal is not None, "受控退出必须记录信号"
        result_code = 128 + shutdown_state.first_signal
    finally:
        try:
            cleanup_errors: list[str] = []
            if playwright_process is not None:
                try:
                    _stop_processes(
                        [playwright_process],
                        sent_signal=shutdown_state.first_signal or signal.SIGTERM,
                        shutdown_state=shutdown_state,
                    )
                except RuntimeError as error:
                    cleanup_errors.append(str(error))
            servers = [
                process
                for process in (uvicorn_process, vite_process)
                if process is not None
            ]
            if servers:
                try:
                    _stop_processes(
                        servers,
                        sent_signal=signal.SIGTERM,
                        shutdown_state=shutdown_state,
                    )
                except RuntimeError as error:
                    cleanup_errors.append(str(error))
                try:
                    _wait_until_ports_released()
                except RuntimeError as error:
                    cleanup_errors.append(str(error))
            if data_dir is not None and not cleanup_errors:
                shutil.rmtree(data_dir)
                print(f"E2E 临时目录已清理：{data_dir}", flush=True)
            if cleanup_errors:
                raise RuntimeError("；".join(cleanup_errors))
        finally:
            for managed_signal, previous_handler in previous_handlers.items():
                signal.signal(managed_signal, previous_handler)
    if shutdown_state.first_signal is not None:
        return 128 + shutdown_state.first_signal
    return result_code


def main() -> int:
    try:
        return run()
    except RuntimeError as error:
        print(f"E2E 运行失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
