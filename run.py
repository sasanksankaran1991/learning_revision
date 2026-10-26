#!/usr/bin/env python3
"""Mac development stays as it is. The Windows PC uses these commands to run production."""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
DATA = ROOT / "data"
DB = DATA / "app.db"
BACKUPS = DATA / "backups"
KEEP_BACKUPS = 14


def python_executable() -> str:
    folder = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    candidate = BACKEND / ".venv" / folder / name
    return str(candidate if candidate.is_file() else Path(sys.executable))


def setup(_args: argparse.Namespace) -> None:
    DATA.mkdir(exist_ok=True)
    subprocess.check_call([python_executable(), "-m", "pip", "install", "-r", str(BACKEND / "requirements.txt")])
    print("Packages installed.")
    print("Next: copy .env.example to .env, then run: python run.py secrets")


def secrets(_args: argparse.Namespace) -> None:
    subprocess.check_call([python_executable(), "-m", "app.cli", "gen-secrets"], cwd=BACKEND)


def build(_args: argparse.Namespace) -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("Install Node.js, then run this again. The website is built with npm.")
    subprocess.check_call([npm, "install"], cwd=FRONTEND)
    subprocess.check_call([npm, "run", "build"], cwd=FRONTEND)
    print(f"Website built at {DIST}")


def serve(args: argparse.Namespace) -> None:
    if not (DIST / "index.html").is_file():
        raise SystemExit("The website has not been built. Run: python run.py build")
    env = os.environ.copy()
    env["SERVE_WEB"] = "true"
    subprocess.check_call(
        [
            python_executable(),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
        ],
        cwd=BACKEND,
        env=env,
    )


def backup(_args: argparse.Namespace) -> None:
    if not DB.is_file():
        raise SystemExit(f"No database at {DB}")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / f"app-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    source = sqlite3.connect(DB)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    copies = sorted(BACKUPS.glob("app-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old in copies[KEEP_BACKUPS:]:
        old.unlink()
    print(dest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Learning Revision on this computer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Install Python packages into backend/.venv")
    sub.add_parser("secrets", help="Print a new JWT secret and encryption key")
    sub.add_parser("build", help="Build the website into frontend/dist")
    serve_parser = sub.add_parser("serve", help="Serve the website and the API on one port")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    sub.add_parser("backup", help="Copy data/app.db and keep the newest 14 copies")
    args = parser.parse_args()
    {"setup": setup, "secrets": secrets, "build": build, "serve": serve, "backup": backup}[args.command](args)


if __name__ == "__main__":
    main()
