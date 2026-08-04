import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

PROJECT_DIR = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_DIR / "scripts" / "run_e2e.py"
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


def test_playwright_uses_parent_runner_and_external_temporary_directory():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))

    assert package["scripts"]["e2e"] == (
        "../backend/.venv/bin/python ../scripts/run_e2e.py"
    )
    assert "A_SHARE_E2E_DATA_DIR" in config
    assert "e2e_backend.py" not in config
    assert "../data/e2e" not in config


def test_parent_runner_uses_unique_directories_and_removes_them_after_each_run(
    monkeypatch, tmp_path
):
    module = load_runner()
    observed_directories: list[Path] = []
    repository_e2e_data = PROJECT_DIR / "data" / "e2e"
    repository_data_before = snapshot_files(repository_e2e_data)

    def fake_run(command, *, cwd, env, check):
        assert command[-1] == "test"
        assert cwd == module.FRONTEND_DIR
        assert check is False
        data_dir = Path(env["A_SHARE_E2E_DATA_DIR"])
        assert data_dir.is_dir()
        assert tmp_path in data_dir.parents
        (data_dir / "e2e.duckdb").write_text("本次运行", encoding="utf-8")
        observed_directories.append(data_dir)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run(temp_root=tmp_path) == 0
    assert not observed_directories[0].exists()
    assert module.run(temp_root=tmp_path) == 0
    assert not observed_directories[1].exists()
    assert observed_directories[0] != observed_directories[1]
    assert snapshot_files(repository_e2e_data) == repository_data_before


def test_parent_runner_removes_temporary_directory_when_playwright_fails(
    monkeypatch, tmp_path
):
    module = load_runner()
    observed: dict[str, Path] = {}

    def fake_run(_command, *, cwd, env, check):
        assert cwd == module.FRONTEND_DIR
        assert check is False
        observed["data_dir"] = Path(env["A_SHARE_E2E_DATA_DIR"])
        assert observed["data_dir"].is_dir()
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    assert module.run(temp_root=tmp_path) == 7
    assert not observed["data_dir"].exists()
