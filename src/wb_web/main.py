"""FastAPI application for WB Review AI web SaaS."""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from wb_web import __version__
from wb_web.admin.routes import router as admin_router
from wb_web.auth.routes import router as auth_router
from wb_web.core.config import get_settings
from wb_web.core.logging import configure_logging
from wb_web.db.base import get_engine
from wb_web.shops.routes import router as shops_router

logger = logging.getLogger("wb_web.request")


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, send_default_pii=False, traces_sample_rate=0.0)

    app = FastAPI(title="WB Review AI", version=__version__)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(shops_router)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        request_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        if request.url.path != "/healthz":
            logger.info(
                "request",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        response.headers["X-Request-ID"] = request_id
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz():
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 — readiness must never leak internals
            logger.warning("readiness check failed", exc_info=False)
            return JSONResponse({"status": "unready", "detail": "database"}, status_code=503)
        return {"status": "ready"}

    return app


app = create_app()
