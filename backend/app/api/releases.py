from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.releases import apk_path, release_payload

router = APIRouter(tags=["app"])


@router.get("/api/app/release")
def app_release() -> dict:
    settings = get_settings()
    return release_payload(apk_path().parent, settings.public_base_url)


@router.get("/releases/learning-revision.apk")
def download_apk() -> FileResponse:
    path = apk_path()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No phone update has been published")
    return FileResponse(path, media_type="application/vnd.android.package-archive", filename="learning-revision.apk")
