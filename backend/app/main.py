"""FastAPI application entry point.

Run with: uvicorn app.main:app --reload --port 8765
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .algorithms import registry
from .api import jobs, rasters, scenes, tiles
from .config import FRONTEND_ORIGINS, ensure_dirs
from .storage import local as store


def create_app() -> FastAPI:
    ensure_dirs()
    store.init_db()
    registry.load_builtins()  # populate the algorithm registry in this process

    app = FastAPI(title="LandSimplex", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=FRONTEND_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(rasters.router)
    app.include_router(scenes.router)
    app.include_router(tiles.router)
    app.include_router(jobs.router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "algorithms": [a.name for a in registry.list_all()]}

    return app


app = create_app()
