from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse


def install_web(app: FastAPI, dist: Path) -> None:
    """Serve the built website from the same process as the API."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise FileNotFoundError(f"Built website not found at {index}. Run: python run.py build")

    @app.get("/{full_path:path}", include_in_schema=False)
    def website(full_path: str):
        return FileResponse(web_file(root, full_path))


def web_file(dist: Path, full_path: str) -> Path:
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404)
    root = dist.resolve()
    candidate = (root / full_path).resolve() if full_path else root
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=404) from None
    if full_path and candidate.is_file():
        return candidate
    return root / "index.html"
