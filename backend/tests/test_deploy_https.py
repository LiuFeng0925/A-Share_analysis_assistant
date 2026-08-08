import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
HTTPS_SCRIPT = PROJECT_ROOT / "deploy" / "configure_https.sh"


def test_auto_mode_installs_existing_certificate_without_contacting_acme(tmp_path):
    domain = "astock.example.com"
    live_dir = tmp_path / "letsencrypt" / "live"
    certificate_dir = live_dir / domain
    certificate_dir.mkdir(parents=True)
    (certificate_dir / "fullchain.pem").write_text("测试证书", encoding="utf-8")

    command_log = tmp_path / "certbot.log"
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    certbot = mock_bin / "certbot"
    certbot.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$CERTBOT_COMMAND_LOG\"\n",
        encoding="utf-8",
    )
    certbot.chmod(0o755)

    result = subprocess.run(
        ["bash", str(HTTPS_SCRIPT), domain, "auto"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{mock_bin}:{os.environ['PATH']}",
            "CERTBOT_LIVE_DIR": str(live_dir),
            "CERTBOT_COMMAND_LOG": str(command_log),
        },
    )

    assert result.returncode == 0, result.stderr
    assert command_log.read_text(encoding="utf-8").strip() == (
        f"install --nginx --cert-name {domain} --redirect --non-interactive"
    )
