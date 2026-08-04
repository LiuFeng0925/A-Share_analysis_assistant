import importlib.util
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_DIR / "scripts" / "e2e_backend.py"
PLAYWRIGHT_CONFIG = PROJECT_DIR / "frontend" / "playwright.config.ts"


def load_runner():
    spec = importlib.util.spec_from_file_location("e2e_backend", RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_playwright_uses_temporary_backend_runner_instead_of_repository_data():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")

    assert "e2e_backend.py" in config
    assert "../data/e2e" not in config


def test_backend_runner_passes_a_unique_temporary_data_directory(monkeypatch, tmp_path):
    module = load_runner()
    temporary_data = tmp_path / "本次独立数据库"
    recorded: dict[str, object] = {}

    class FakeTemporaryDirectory:
        def __init__(self, *, prefix):
            recorded["prefix"] = prefix

        def __enter__(self):
            temporary_data.mkdir()
            return str(temporary_data)

        def __exit__(self, *_args):
            recorded["cleaned"] = True

    class FakeProcess:
        def __init__(self, command, env):
            recorded["command"] = command
            recorded["env"] = env

        def wait(self):
            return 0

        def poll(self):
            return 0

        def terminate(self):
            recorded["terminated"] = True

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", FakeTemporaryDirectory)
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)

    assert module.run(port=18000) == 0
    environment = recorded["env"]
    assert environment["A_SHARE_DATA_DIR"] == str(temporary_data)
    assert environment["A_SHARE_FIXTURE_SOURCE"] == "true"
    assert recorded["cleaned"] is True
