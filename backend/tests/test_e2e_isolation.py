import importlib.util
import json
import os
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
SIGNAL_FIXTURE = Path(__file__).parent / "fixtures" / "e2e_signal_process.py"
PLAYWRIGHT_CONFIG = PROJECT_DIR / "frontend" / "playwright.config.ts"
PACKAGE_JSON = PROJECT_DIR / "frontend" / "package.json"


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


def wait_for_file_or_runner_failure(
    path: Path, runner: subprocess.Popen[str], timeout: float = 8
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if runner.poll() is not None:
            stdout, stderr = runner.communicate()
            pytest.fail(
                f"E2E runner 在测试替身就绪前退出：{runner.returncode}\n"
                f"stdout={stdout}\nstderr={stderr}"
            )
        time.sleep(0.02)
    pytest.fail("等待 E2E 信号测试替身就绪超时")


def reserve_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def assert_port_released(port: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", port))
            return
        except OSError:
            time.sleep(0.02)
    pytest.fail(f"E2E 信号测试端口未释放：{port}")


def test_playwright_uses_platform_aware_parent_runner_and_python_module():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert package["scripts"]["e2e"] == "node ../scripts/run_e2e.mjs"
    assert RUNNER_LAUNCHER.is_file()
    assert "process.platform" in RUNNER_LAUNCHER.read_text(encoding="utf-8")
    assert "A_SHARE_E2E_DATA_DIR" in config
    assert "A_SHARE_E2E_PYTHON" in config
    assert "-m uvicorn" in config
    assert ".venv/bin" not in config
    assert "e2e_backend.py" not in config
    assert "../data/e2e" not in config


def test_default_playwright_command_uses_pnpm_exec_without_recursive_e2e():
    module = load_runner()

    command = module.default_playwright_command()

    assert Path(command[0]).name in {"pnpm", "pnpm.cmd"}
    assert command[1:] == ["exec", "playwright", "test"]
    assert "e2e" not in command[1:]


def test_parent_runner_does_not_leave_directory_when_pnpm_resolution_fails(
    monkeypatch, tmp_path
):
    module = load_runner()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match="未找到 pnpm"):
        module.run(temp_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_parent_runner_uses_unique_directories_and_removes_them_after_each_run(
    tmp_path,
):
    module = load_runner()
    audit_file = tmp_path / "正常运行目录.jsonl"
    child_code = (
        "import json, os, sys; "
        "path = os.environ['A_SHARE_E2E_DATA_DIR']; "
        "open(sys.argv[1], 'a', encoding='utf-8').write(json.dumps(path) + '\\n')"
    )
    command = [sys.executable, "-c", child_code, str(audit_file)]
    repository_e2e_data = PROJECT_DIR / "data" / "e2e"
    repository_data_before = snapshot_files(repository_e2e_data)

    assert module.run(temp_root=tmp_path, command=command) == 0
    assert module.run(temp_root=tmp_path, command=command) == 0

    observed_directories = [
        Path(json.loads(line)) for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert len(observed_directories) == 2
    assert observed_directories[0] != observed_directories[1]
    assert all(not directory.exists() for directory in observed_directories)
    assert snapshot_files(repository_e2e_data) == repository_data_before


def test_parent_runner_removes_temporary_directory_when_playwright_fails(tmp_path):
    module = load_runner()
    audit_file = tmp_path / "失败运行目录.txt"
    child_code = (
        "import os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text("
        "os.environ['A_SHARE_E2E_DATA_DIR'], encoding='utf-8'); "
        "raise SystemExit(7)"
    )

    result = module.run(
        temp_root=tmp_path,
        command=[sys.executable, "-c", child_code, str(audit_file)],
    )

    assert result == 7
    assert not Path(audit_file.read_text(encoding="utf-8")).exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组信号集成测试")
def test_parent_runner_cleans_directory_when_signal_arrives_during_startup(tmp_path):
    record_file = tmp_path / "启动阶段目录.txt"
    child_code = "import time; time.sleep(10)"
    bootstrap = (
        "import sys, time; from pathlib import Path; "
        f"sys.path.insert(0, {str(RUNNER_PATH.parent)!r}); "
        "import run_e2e; original_start = run_e2e._start_process\n"
        "def delayed_start(command, environment):\n"
        f" Path({str(record_file)!r}).write_text("
        "environment['A_SHARE_E2E_DATA_DIR'], encoding='utf-8')\n"
        " time.sleep(0.5)\n"
        " return original_start(command, environment)\n"
        "run_e2e._start_process = delayed_start\n"
        "raise SystemExit(run_e2e.run("
        f"temp_root=Path({str(tmp_path)!r}), command=[sys.executable, '-c', "
        f"{child_code!r}], signal_grace_seconds=1, terminate_grace_seconds=1))"
    )
    runner = subprocess.Popen(
        [sys.executable, "-c", bootstrap],
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_file_or_runner_failure(record_file, runner)
        os.kill(runner.pid, signal.SIGTERM)
        runner.communicate(timeout=5)

        assert runner.returncode == 128 + signal.SIGTERM
        assert not Path(record_file.read_text(encoding="utf-8")).exists()
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()


@pytest.mark.skipif(os.name == "nt", reason="POSIX 进程组信号集成测试")
@pytest.mark.parametrize("sent_signal", [signal.SIGTERM, signal.SIGINT])
def test_parent_runner_forwards_signal_and_cleans_process_group(
    tmp_path, sent_signal
):
    record_file = tmp_path / f"信号-{sent_signal}.json"
    port = reserve_port()
    repository_e2e_data = PROJECT_DIR / "data" / "e2e"
    repository_data_before = snapshot_files(repository_e2e_data)
    bootstrap = (
        "import sys; from pathlib import Path; "
        f"sys.path.insert(0, {str(RUNNER_PATH.parent)!r}); "
        "import run_e2e; "
        "raise SystemExit(run_e2e.run("
        f"temp_root=Path({str(tmp_path)!r}), "
        f"command=[sys.executable, {str(SIGNAL_FIXTURE)!r}, {str(port)!r}, "
        f"{str(record_file)!r}], signal_grace_seconds=2, terminate_grace_seconds=1))"
    )
    runner = subprocess.Popen(
        [sys.executable, "-c", bootstrap],
        cwd=PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_file_or_runner_failure(record_file, runner)
        record = json.loads(record_file.read_text(encoding="utf-8"))

        os.kill(runner.pid, sent_signal)
        stdout, stderr = runner.communicate(timeout=10)

        assert runner.returncode == 128 + sent_signal
        assert "E2E 临时目录已清理" in stdout
        assert record["playwright_pgid"] == record["playwright_pid"]
        assert record["webserver_pgid"] == record["playwright_pgid"]
        assert not Path(record["data_dir"]).exists()
        assert snapshot_files(repository_e2e_data) == repository_data_before
        assert_port_released(port)
        for process_id in (record["playwright_pid"], record["webserver_pid"]):
            with pytest.raises(ProcessLookupError):
                os.kill(process_id, 0)
        assert "Traceback" not in stderr
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
