"""FastAPI application for WB Review AI web SaaS."""

from __future__ import annotations

from fastapi import FastAPI

from wb_web import __version__
from wb_web.auth.routes import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(title="WB Review AI", version=__version__)
    app.include_router(auth_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
