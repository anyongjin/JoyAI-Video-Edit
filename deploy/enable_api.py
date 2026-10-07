"""Install the API on the existing GPU deployment without reinstalling dependencies."""

import argparse
import os
import shlex
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="connect.westd.seetacloud.com")
    parser.add_argument("--port", type=int, default=15298)
    parser.add_argument("--backend-env", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    remote = "/root/autodl-tmp/JoyAI-Video-Edit"
    ssh = ["ssh", "-o", "BatchMode=yes", "-p", str(args.port), "root@" + args.host]
    subprocess.run(ssh + [f"mkdir -p {remote}/deploy/api-staging"], check=True)
    for source, target in [
        (root / "xvideo/serving/live_api.py", "live_api.py"),
        (
            root / "xvideo/serving/serve_joyomni_streaming.py",
            "serve_joyomni_streaming.py",
        ),
        (root / "kit/ops/serve.sh", "serve.sh"),
    ]:
        subprocess.run(
            [
                "scp",
                "-P",
                str(args.port),
                str(source),
                f"root@{args.host}:{remote}/deploy/api-staging/{target}",
            ],
            check=True,
        )
    provision = (
        "import os,secrets; from pathlib import Path; "
        "p=Path('/root/autodl-tmp/joyai-api.key'); os.umask(0o077); "
        "p.write_text(secrets.token_urlsafe(48)) if not p.exists() else None; "
        "p.chmod(0o600); print(p.read_text().strip())"
    )
    key = subprocess.run(
        ssh + ["/root/autodl-tmp/joyai-env/bin/python -c " + shlex.quote(provision)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if args.backend_env:
        env = args.backend_env
        original = env.read_text() if env.exists() else ""
        lines = [
            line
            for line in original.splitlines()
            if not line.startswith(("JOYAI_API_KEY=", "JOYAI_API_URL="))
        ]
        lines += ["JOYAI_API_URL=https://joyai.nuvatech.cn", "JOYAI_API_KEY=" + key]
        env.write_text("\n".join(lines) + "\n")
        os.chmod(env, 0o600)
    activate = f"""set -eu
cd {remote}/deploy
cp -p xvideo/serving/serve_joyomni_streaming.py api-staging/serve_joyomni_streaming.before-api.py
cp -p ops/serve.sh api-staging/serve.before-api.sh
cp api-staging/live_api.py xvideo/serving/live_api.py
cp api-staging/serve_joyomni_streaming.py xvideo/serving/serve_joyomni_streaming.py
cp api-staging/serve.sh ops/serve.sh
/root/autodl-tmp/joyai-env/bin/python -m py_compile xvideo/serving/live_api.py xvideo/serving/serve_joyomni_streaming.py
/usr/bin/supervisord ctl -s http://127.0.0.1:19090 restart joyai-demo
"""
    subprocess.run(ssh + [activate], check=True)
    print("API installed; key stored with mode 0600. Wait for /health before testing.")


if __name__ == "__main__":
    main()
