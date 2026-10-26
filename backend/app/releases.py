from __future__ import annotations

import json
from pathlib import Path

from app.config import ROOT

RELEASES = ROOT / "data" / "releases"
APK_NAME = "learning-revision.apk"


def apk_path() -> Path:
    return RELEASES / APK_NAME


def release_payload(directory: Path, public_base_url: str) -> dict:
    """Describe a phone package only when that file is actually on this machine."""
    apk = directory / APK_NAME
    empty = {"version_code": 0, "version_name": "", "apk_url": None, "notes": ""}
    if not apk.is_file():
        return empty
    meta_file = directory / "release.json"
    meta: dict = {}
    if meta_file.is_file():
        try:
            loaded = json.loads(meta_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except json.JSONDecodeError:
            return empty
    try:
        version_code = int(meta.get("version_code") or 0)
    except (TypeError, ValueError):
        return empty
    if version_code <= 0:
        return empty
    base = public_base_url.rstrip("/")
    return {
        "version_code": version_code,
        "version_name": str(meta.get("version_name") or ""),
        "apk_url": f"{base}/releases/{APK_NAME}",
        "notes": str(meta.get("notes") or "")[:500],
    }
