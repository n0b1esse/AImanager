"""FastAPI application for WB Review AI web SaaS.

Phase 0/1 scaffold: health check only. Auth, shops, reviews, billing
land in later phases — see docs/PLAN.md.
"""

from __future__ import annotations

from fastapi import FastAPI

from wb_web import __version__


def create_app() -> FastAPI:
    app = FastAPI(title="WB Review AI", version=__version__)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
