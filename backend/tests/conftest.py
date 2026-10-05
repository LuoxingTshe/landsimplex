"""Shared pytest fixtures.

The API + storage tests redirect every data path to a per-test tmp_path so the
production SQLite + COG dirs are never touched. Pure-function tests (e.g.
test_sweep.py) don't need any fixture and import the module directly.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_data_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every config path at a tmp_path. Returns the patched config module.

    Must be requested BEFORE importing app.storage.local (or anything that
    transitively imports it) so the module-level `DB_PATH` is read against the
    patched values. The fixture reloads the storage module to be safe.
    """
    data = tmp_path / "data"
    cog = data / "cogs"
    result = data / "results"
    upload = data / "uploads_tmp"
    bundle = data / "bundle_staging"
    for d in (cog, result, upload, bundle):
        d.mkdir(parents=True)

    from app import config as cfg
    monkeypatch.setattr(cfg, "COG_DIR", cog)
    monkeypatch.setattr(cfg, "RESULT_DIR", result)
    monkeypatch.setattr(cfg, "UPLOAD_TMP", upload)
    monkeypatch.setattr(cfg, "BUNDLE_STAGING_DIR", bundle)
    monkeypatch.setattr(cfg, "DB_PATH", data / "landsimplex.db")

    # Reload storage so DB_PATH is re-read.
    for modname in ("app.storage.local", "app.storage.migrations"):
        if modname in sys.modules:
            importlib.reload(sys.modules[modname])

    from app.storage import local as store
    store.init_db()
    return cfg
