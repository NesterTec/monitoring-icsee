import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from nvr.auth import seed_admin
from nvr.config import get_settings
import nvr.db as db_module
from nvr.db import init_db
from nvr.go2rtc_sync import sync_go2rtc
from nvr.recorder import recorder_manager
from nvr.routers.web import router as web_router
from nvr.settings_service import ensure_defaults

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    assert db_module.SessionLocal is not None
    db = db_module.SessionLocal()
    try:
        seed_admin(db)
        ensure_defaults(db)
        sync_go2rtc(db)
        recorder_manager.start_background()
        recorder_manager.reconcile(db)
    finally:
        db.close()
    yield
    recorder_manager.stop_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ICSEE NVR", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie="icsee_nvr")
    static_dir = Path(__file__).resolve().parent / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
