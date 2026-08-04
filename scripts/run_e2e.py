"""在父进程中管理 Playwright 独立数据目录的完整生命周期。"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_DIR / "frontend"
PLAYWRIGHT_BIN = FRONTEND_DIR / "node_modules" / ".bin" / "playwright"
TEMP_PREFIX = "a-share-radar-e2e-"


def run(*, temp_root: Path | None = None) -> int:
    data_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=temp_root))
    environment = os.environ.copy()
    environment["A_SHARE_E2E_DATA_DIR"] = str(data_dir)
    command = [str(PLAYWRIGHT_BIN), "test"]
    print(f"E2E 临时目录：{data_dir}", flush=True)
    try:
        result = subprocess.run(
            command,
            cwd=FRONTEND_DIR,
            env=environment,
            check=False,
        )
        return result.returncode
    finally:
        shutil.rmtree(data_dir)
        print(f"E2E 临时目录已清理：{data_dir}", flush=True)


if __name__ == "__main__":
    raise SystemExit(run())
