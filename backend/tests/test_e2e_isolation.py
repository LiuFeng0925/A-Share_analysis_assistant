import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_DIR / "scripts" / "run_e2e.py"
RUNNER_LAUNCHER = PROJECT_DIR / "scripts" / "run_e2e.mjs"
PLAYWRIGHT_CONFIG = PROJECT_DIR / "frontend" / "playwright.config.ts"
PACKAGE_JSON = PROJECT_DIR / "frontend" / "package.json"
REPOSITORY_E2E_DATA = PROJECT_DIR / "data" / "e2e"
SERVICE_PORTS = (18000, 4173)
PID_LABELS = ("runner", "Uvicorn", "Vite", "Playwright")


def load_runner():
    spec = importlib.util.spec_from_file_location("run_e2e", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot_files(directory: Path) -> dict[Path, bytes]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            return True
    except OSError:
        return False


def assert_port_released(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
            return
        except OSError:
            time.sleep(0.02)
    pytest.fail(f"E2E 测试端口未释放：{port}")


def process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
        return True
    except ProcessLookupError:
        return False


def wait_for_process_exit(process_id: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not process_exists(process_id):
            return
        time.sleep(0.02)
    pytest.fail(f"E2E 进程仍未退出：{process_id}")


def wait_for_process_group_exit(process_group_id: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail(f"E2E 进程组仍未退出：{process_group_id}")


def wait_for_runtime_log(
    log_path: Path, launcher: subprocess.Popen[bytes], timeout: float = 20
) -> str:
    required = [f"E2E {label} PID：" for label in PID_LABELS]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        content = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if all(text in content for text in required):
            return content
        if launcher.poll() is not None:
            pytest.fail(
                f"Node E2E 启动器在三棵进程树就绪前退出：{launcher.returncode}\n{content}"
            )
        time.sleep(0.05)
    pytest.fail("等待 Node E2E 三棵进程树就绪超时")


def parse_runtime(content: str) -> tuple[dict[str, int], Path]:
    process_ids = {
        label: int(match.group(1))
        for label in PID_LABELS
        if (match := re.search(rf"E2E {label} PID：(\d+)", content))
    }
    data_match = re.search(r"E2E 临时目录：(.+)", content)
    assert set(process_ids) == set(PID_LABELS)
    assert data_match is not None
    return process_ids, Path(data_match.group(1).strip())


def test_playwright_config_has_no_webserver_and_uses_parent_runner():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert package["scripts"]["e2e"] == "node ../scripts/run_e2e.mjs"
    assert RUNNER_LAUNCHER.is_file()
    assert "process.platform" in RUNNER_LAUNCHER.read_text(encoding="utf-8")
    assert "webServer" not in config
    assert "uvicorn" not in config
    assert "A_SHARE_E2E_DATA_DIR" not in config
    assert "../data/e2e" not in config


def test_default_commands_resolve_three_runner_owned_processes():
    module = load_runner()

    uvicorn = module.default_uvicorn_command()
    vite = module.default_vite_command()
    playwright = module.default_playwright_command()

    assert uvicorn[:3] == [sys.executable, "-m", "uvicorn"]
    assert Path(vite[0]).name.lower() in {"pnpm", "pnpm.cmd"}
    assert vite[1:3] == ["exec", "vite"]
    assert Path(playwright[0]).name.lower() in {"pnpm", "pnpm.cmd"}
    assert playwright[1:] == ["exec", "playwright", "test"]
    assert "e2e" not in playwright[1:]


def test_parent_runner_does_not_leave_directory_when_command_resolution_fails(
    monkeypatch, tmp_path
):
    module = load_runner()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="未找到 pnpm"):
        module.run(temp_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_parent_runner_preserves_success_and_failure_codes_and_unique_directories(
    tmp_path,
):
    module = load_runner()
    audit_file = tmp_path / "运行目录.jsonl"
    child_code = (
        "import json, os, sys; "
        "path = os.environ['A_SHARE_E2E_DATA_DIR']; "
        "open(sys.argv[1], 'a', encoding='utf-8').write(json.dumps(path) + '\\n'); "
        "raise SystemExit(int(sys.argv[2]))"
    )
    repository_data_before = snapshot_files(REPOSITORY_E2E_DATA)

    for expected_code in (0, 7):
        result = module.run(
            temp_root=tmp_path,
            playwright_command=[
                sys.executable,
                "-c",
                child_code,
                str(audit_file),
                str(expected_code),
            ],
        )
        assert result == expected_code

    observed_directories = [
        Path(json.loads(line)) for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len(observed_directories) == 2
    assert observed_directories[0] != observed_directories[1]
    assert all(not directory.exists() for directory in observed_directories)
    assert snapshot_files(REPOSITORY_E2E_DATA) == repository_data_before
    for port in SERVICE_PORTS:
        assert_port_released(port)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组生命周期测试")
def test_stop_processes_cleans_descendants_after_tree_root_has_exited(tmp_path):
    module = load_runner()
    child_pid_path = tmp_path / "子进程.pid"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)']); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')"
    )
    parent = module._start_process(
        [sys.executable, "-c", parent_code, str(child_pid_path)],
        cwd=PROJECT_DIR,
        environment=os.environ.copy(),
    )
    child_pid: int | None = None
    try:
        parent.wait(timeout=5)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert process_exists(child_pid)

        module._stop_processes(
            [parent],
            sent_signal=signal.SIGTERM,
            shutdown_state=module.ShutdownState(),
        )

        wait_for_process_exit(child_pid)
    finally:
        if child_pid is not None and process_exists(child_pid):
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_preflight_occupied_port_is_not_mistaken_for_owned_cleanup(
    monkeypatch, tmp_path
):
    module = load_runner()
    monkeypatch.setattr(module, "PORT_RELEASE_TIMEOUT_SECONDS", 0.05)

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", SERVICE_PORTS[0]))
        listener.listen()

        with pytest.raises(RuntimeError, match="E2E 启动前端口已被占用：18000"):
            module.run(temp_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX 清理阶段信号测试")
def test_signal_received_during_cleanup_preserves_signal_exit_code(
    monkeypatch, tmp_path
):
    module = load_runner()
    original_wait = module._wait_until_ports_released
    signal_sent = False

    def signal_during_cleanup() -> None:
        nonlocal signal_sent
        if not signal_sent:
            signal_sent = True
            os.kill(os.getpid(), signal.SIGTERM)
        original_wait()

    monkeypatch.setattr(module, "_wait_until_ports_released", signal_during_cleanup)

    result = module.run(
        temp_root=tmp_path,
        playwright_command=[sys.executable, "-c", "raise SystemExit(0)"],
    )

    assert result == 128 + signal.SIGTERM
    assert signal_sent
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX 真实信号集成测试")
@pytest.mark.parametrize("sent_signal", [signal.SIGTERM, signal.SIGINT])
def test_real_node_launcher_signal_cleans_all_owned_process_trees(
    tmp_path, sent_signal
):
    node = shutil.which("node")
    assert node is not None
    for port in SERVICE_PORTS:
        assert_port_released(port)
    repository_data_before = snapshot_files(REPOSITORY_E2E_DATA)
    log_path = tmp_path / f"node-signal-{sent_signal}.log"
    launcher: subprocess.Popen[bytes] | None = None
    process_ids: dict[str, int] = {}
    data_dir: Path | None = None
    try:
        with log_path.open("wb") as output:
            launcher = subprocess.Popen(
                [node, str(RUNNER_LAUNCHER)],
                cwd=PROJECT_DIR,
                env=os.environ.copy(),
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            content = wait_for_runtime_log(log_path, launcher)
            process_ids, data_dir = parse_runtime(content)
            assert all(port_is_listening(port) for port in SERVICE_PORTS)

            os.kill(launcher.pid, sent_signal)
            time.sleep(0.05)
            assert launcher.poll() is None
            os.kill(launcher.pid, sent_signal)
            launcher.wait(timeout=20)

        content = log_path.read_text(encoding="utf-8")
        assert launcher.returncode == 128 + sent_signal
        assert "E2E 临时目录已清理" in content
        assert data_dir is not None and not data_dir.exists()
        for process_id in process_ids.values():
            wait_for_process_exit(process_id)
            wait_for_process_group_exit(process_id)
        for port in SERVICE_PORTS:
            assert_port_released(port)
        assert snapshot_files(REPOSITORY_E2E_DATA) == repository_data_before
    finally:
        if launcher is not None and launcher.poll() is None:
            launcher.kill()
            launcher.wait()
        for process_id in process_ids.values():
            if process_exists(process_id):
                try:
                    os.killpg(process_id, signal.SIGKILL)
                except ProcessLookupError:
                    pass
