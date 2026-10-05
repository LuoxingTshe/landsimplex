"""Centralised paths and settings. Everything writes under DATA_DIR."""
from __future__ import annotations

from pathlib import Path

# Resolve relative to this file, not CWD - so it works regardless of where uvicorn is launched
APP_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = APP_ROOT.parent
DATA_DIR = BACKEND_ROOT / "data"

COG_DIR = DATA_DIR / "cogs"                    # all rasters stored as COG here
RESULT_DIR = DATA_DIR / "results"              # algorithm outputs (also COG)
UPLOAD_TMP = DATA_DIR / "uploads_tmp"          # incoming files before cogification
BUNDLE_STAGING_DIR = DATA_DIR / "bundle_staging"  # extracted scene archives
DB_PATH = DATA_DIR / "metadata.sqlite"

# Default tile rendering window when frontend doesn't specify min/max
DEFAULT_RESCALE = (0, 4000)  # metres, suitable for most DEMs

HOST = "127.0.0.1"
PORT = 8765

# CORS origins for the dev frontend
FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def ensure_dirs() -> None:
    for d in (DATA_DIR, COG_DIR, RESULT_DIR, UPLOAD_TMP, BUNDLE_STAGING_DIR):
        d.mkdir(parents=True, exist_ok=True)
