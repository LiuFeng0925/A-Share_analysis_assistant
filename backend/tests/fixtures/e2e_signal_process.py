"""模拟 Playwright 及其占用端口的 webServer 子进程。"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

SERVER_CODE = """
import http.server
import signal
import sys

def stop(received_signal, _frame):
    raise SystemExit(128 + received_signal)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
server = http.server.ThreadingHTTPServer(
    ("127.0.0.1", int(sys.argv[1])),
    http.server.SimpleHTTPRequestHandler,
)
server.serve_forever()
"""

port = int(sys.argv[1])
record_file = Path(sys.argv[2])
webserver = subprocess.Popen([sys.executable, "-c", SERVER_CODE, str(port)])


def exit_after_group_signal(received_signal, _frame) -> None:
    try:
        webserver.wait(timeout=3)
    except subprocess.TimeoutExpired:
        webserver.kill()
        webserver.wait()
    raise SystemExit(128 + received_signal)


signal.signal(signal.SIGTERM, exit_after_group_signal)
signal.signal(signal.SIGINT, exit_after_group_signal)

deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
            break
    except OSError:
        if webserver.poll() is not None:
            raise SystemExit(webserver.returncode)
        time.sleep(0.02)
else:
    webserver.kill()
    webserver.wait()
    raise SystemExit("模拟 webServer 启动超时")

record_file.write_text(
    json.dumps(
        {
            "data_dir": os.environ["A_SHARE_E2E_DATA_DIR"],
            "playwright_pid": os.getpid(),
            "playwright_pgid": os.getpgid(0),
            "webserver_pid": webserver.pid,
            "webserver_pgid": os.getpgid(webserver.pid),
            "port": port,
        }
    ),
    encoding="utf-8",
)
raise SystemExit(webserver.wait())
