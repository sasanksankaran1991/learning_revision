from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import admin, auth, credentials, library, sessions, settings, telegram
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.services.accounts import ensure_bootstrap_admin
from app.services.telegram_summary import align_summary_time, ensure_telegram_columns, start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
        ensure_telegram_columns(db)
        align_summary_time(db)
    finally:
        db.close()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Learning Revision", version="0.1.0", lifespan=lifespan)
settings_obj = get_settings()
_CAPACITOR_ORIGINS = ("https://localhost", "http://localhost", "capacitor://localhost")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys([*settings_obj.cors_origin_list, *_CAPACITOR_ORIGINS])),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(credentials.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(telegram.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}
