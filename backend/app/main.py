"""FastAPI application entry point.

Run with: uvicorn app.main:app --reload --port 8765
"""
from __future__ import annotations

import multiprocessing
import os
import signal
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .algorithms import registry
from .api import jobs, rasters, scenes, tiles
from .config import FRONTEND_ORIGINS, ensure_dirs
from .jobs import runner
from .storage import local as store


def _terminate_server() -> None:
    runner.shutdown()
    # Under `--reload` this process is a spawned worker: signalling the reloader
    # parent makes it exit and terminate us. Otherwise uvicorn handles SIGTERM itself.
    target = os.getppid() if multiprocessing.parent_process() is not None else os.getpid()
    os.kill(target, signal.SIGTERM)


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

    @app.post("/shutdown", status_code=202)
    def shutdown(request: Request) -> dict:
        # A plain cross-site POST skips the CORS preflight, so check Origin here.
        origin = request.headers.get("origin")
        if origin is not None and origin not in FRONTEND_ORIGINS:
            raise HTTPException(status_code=403, detail="forbidden origin")
        # Delay so this response is flushed before the process goes down.
        threading.Timer(0.3, _terminate_server).start()
        return {"status": "shutting_down"}

    return app


app = create_app()
