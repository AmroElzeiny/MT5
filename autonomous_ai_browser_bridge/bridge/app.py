from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .config import settings
from .openai_compat import router as openai_router
from .webapp import router as web_router
from .folder_gateway import run as run_folder_gateway
from .browser import status as browser_status
from .browser_worker import autonomous_browser_worker
from .audit import log_event

_stop = threading.Event()
_folder_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _folder_thread
    log_event("bridge_start", base_url=settings.base_url, autonomous=settings.browser_automation_enabled)
    autonomous_browser_worker.start()
    if settings.folder_gateway_enabled:
        _folder_thread = threading.Thread(target=run_folder_gateway, args=(_stop,), daemon=True, name="folder-gateway")
        _folder_thread.start()
    yield
    _stop.set()
    autonomous_browser_worker.stop()
    log_event("bridge_stop")


app = FastAPI(title="Autonomous AI Browser Bridge", version="2.1.0", lifespan=lifespan)
app.include_router(openai_router, prefix="/v1")
app.include_router(web_router)


@app.get("/health")
def health():
    return {
        "ok": True,
        "base_url": settings.base_url,
        "max_pending_jobs": settings.max_pending_jobs,
        "hard_timeout_sec": settings.hard_timeout_sec,
        "autonomous": settings.browser_automation_enabled,
        "browser_configured": settings.browser_configured,
        "browser": browser_status(),
    }
