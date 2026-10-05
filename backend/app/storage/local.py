"""Metadata storage. SQLite, two tables: rasters and jobs.

Design notes:
- Each call opens its own connection. sqlite3 connections aren't safe across threads,
  and FastAPI may dispatch handlers on different threads via the threadpool.
- We don't use SQLAlchemy - the schema is too simple to justify it for MVP.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from ..config import DB_PATH
from .migrations import run_migrations

SCHEMA = """
CREATE TABLE IF NOT EXISTS rasters (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    cog_path    TEXT NOT NULL,
    crs         TEXT NOT NULL,
    bounds      TEXT NOT NULL,        -- json [minx, miny, maxx, maxy] in native CRS
    bounds_wgs84 TEXT NOT NULL,       -- json [minx, miny, maxx, maxy] for the map fit
    resolution  TEXT NOT NULL,        -- json [xres, yres]
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    dtype       TEXT NOT NULL,
    nodata      REAL,
    kind        TEXT NOT NULL DEFAULT 'source',  -- 'source' or 'result'
    semantic    TEXT NOT NULL DEFAULT 'continuous',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    algorithm   TEXT NOT NULL,
    params      TEXT NOT NULL,        -- json
    inputs      TEXT NOT NULL,        -- json {role: raster_id, ...}
    status      TEXT NOT NULL,        -- pending|running|succeeded|failed
    progress    REAL NOT NULL DEFAULT 0.0,
    message     TEXT,
    output_id   TEXT,                 -- raster_id of the result, when succeeded
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as c:
        c.executescript(SCHEMA)
        run_migrations(c)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- rasters ---

def insert_raster(
    *,
    name: str,
    cog_path: Path,
    crs: str,
    bounds: tuple[float, float, float, float],
    bounds_wgs84: tuple[float, float, float, float],
    resolution: tuple[float, float],
    width: int,
    height: int,
    dtype: str,
    nodata: Optional[float],
    kind: str = "source",
    semantic: str = "continuous",
    scene_id: Optional[str] = None,
    sensor: Optional[str] = None,
    band_id: Optional[str] = None,
    auto_role: Optional[str] = None,
    role: Optional[str] = None,
    resolution_m: Optional[int] = None,
    stats_min: Optional[float] = None,
    stats_max: Optional[float] = None,
) -> str:
    raster_id = uuid.uuid4().hex[:12]
    with connect() as c:
        c.execute(
            "INSERT INTO rasters (id, name, cog_path, crs, bounds, bounds_wgs84, resolution, "
            "width, height, dtype, nodata, kind, semantic, created_at, "
            "scene_id, sensor, band_id, auto_role, role, resolution_m, stats_min, stats_max) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                raster_id,
                name,
                str(cog_path),
                crs,
                json.dumps(list(bounds)),
                json.dumps(list(bounds_wgs84)),
                json.dumps(list(resolution)),
                width,
                height,
                dtype,
                nodata,
                kind,
                semantic,
                _now(),
                scene_id,
                sensor,
                band_id,
                auto_role,
                role,
                resolution_m,
                stats_min,
                stats_max,
            ),
        )
    return raster_id


def get_raster(raster_id: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        row = c.execute("SELECT * FROM rasters WHERE id = ?", (raster_id,)).fetchone()
    if row is None:
        return None
    return _row_to_raster(row)


def list_rasters() -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT * FROM rasters ORDER BY created_at DESC").fetchall()
    return [_row_to_raster(r) for r in rows]


def _row_to_raster(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["bounds"] = json.loads(d["bounds"])
    d["bounds_wgs84"] = json.loads(d["bounds_wgs84"])
    d["resolution"] = json.loads(d["resolution"])
    return d


# --- jobs ---

def insert_job(
    algorithm: str,
    params: dict,
    inputs: dict[str, str],
    *,
    kind: str = "single",
    parent_id: Optional[str] = None,
    sample_label: Optional[str] = None,
    sample_index: Optional[int] = None,
) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with connect() as c:
        c.execute(
            "INSERT INTO jobs (id, algorithm, params, inputs, status, progress, "
            "created_at, updated_at, kind, parent_id, sample_label, sample_index) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                algorithm,
                json.dumps(params),
                json.dumps(inputs),
                "pending",
                0.0,
                now,
                now,
                kind,
                parent_id,
                sample_label,
                sample_index,
            ),
        )
    return job_id


def insert_sweep_job(
    algorithm: str,
    base_params: dict,
    param_ranges: dict,
    inputs: dict[str, str],
    sample_count: int,
) -> str:
    """Insert the parent record for a parameter sweep.

    `base_params` is the user-supplied fixed params; concrete combos live on
    the children. `param_ranges` is the raw JSON (already validated) kept for
    UI re-display.
    """
    job_id = uuid.uuid4().hex[:12]
    now = _now()
    with connect() as c:
        c.execute(
            "INSERT INTO jobs (id, algorithm, params, inputs, status, progress, "
            "created_at, updated_at, kind, param_ranges, sample_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                algorithm,
                json.dumps(base_params),
                json.dumps(inputs),
                "pending",
                0.0,
                now,
                now,
                "sweep",
                json.dumps(param_ranges),
                sample_count,
            ),
        )
    return job_id


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[float] = None,
    message: Optional[str] = None,
    output_id: Optional[str] = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if message is not None:
        fields.append("message = ?")
        values.append(message)
    if output_id is not None:
        fields.append("output_id = ?")
        values.append(output_id)
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(job_id)
    with connect() as c:
        c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        row = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        d = _row_to_job(dict(row))
        if d.get("kind") == "sweep":
            children = c.execute(
                "SELECT id FROM jobs WHERE parent_id = ? ORDER BY sample_index", (job_id,)
            ).fetchall()
            d["child_ids"] = [r["id"] for r in children]
    return d


def list_children(parent_id: str) -> list[dict[str, Any]]:
    """All child jobs of a sweep, ordered by sample_index."""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE parent_id = ? ORDER BY sample_index", (parent_id,)
        ).fetchall()
    return [_row_to_job(dict(r)) for r in rows]


def _row_to_job(d: dict[str, Any]) -> dict[str, Any]:
    d["params"] = json.loads(d["params"])
    d["inputs"] = json.loads(d["inputs"])
    if d.get("param_ranges"):
        d["param_ranges"] = json.loads(d["param_ranges"])
    return d


# --- scenes ---

def insert_scene(scene_id: str, sensor: str, acquired_at: Optional[str], raw_name: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO scenes (scene_id, sensor, acquired_at, raw_name, created_at) "
            "VALUES (?,?,?,?,?)",
            (scene_id, sensor, acquired_at, raw_name, _now()),
        )


def scene_exists(scene_id: str) -> bool:
    with connect() as c:
        row = c.execute("SELECT 1 FROM scenes WHERE scene_id = ?", (scene_id,)).fetchone()
    return row is not None


def get_scene(scene_id: str) -> Optional[dict[str, Any]]:
    with connect() as c:
        row = c.execute("SELECT * FROM scenes WHERE scene_id = ?", (scene_id,)).fetchone()
        if row is None:
            return None
        scene = dict(row)
        bands = c.execute(
            "SELECT * FROM rasters WHERE scene_id = ? ORDER BY band_id ASC", (scene_id,)
        ).fetchall()
    scene["bands"] = [_row_to_raster(b) for b in bands]
    return scene


def list_scenes() -> list[dict[str, Any]]:
    with connect() as c:
        rows = c.execute("SELECT * FROM scenes ORDER BY created_at DESC").fetchall()
        scenes = []
        for row in rows:
            scene = dict(row)
            bands = c.execute(
                "SELECT * FROM rasters WHERE scene_id = ? ORDER BY band_id ASC", (scene["scene_id"],)
            ).fetchall()
            scene["bands"] = [_row_to_raster(b) for b in bands]
            scenes.append(scene)
    return scenes


def update_raster_role(raster_id: str, role: str) -> None:
    with connect() as c:
        c.execute("UPDATE rasters SET role = ? WHERE id = ?", (role, raster_id))


def find_raster_references(raster_id: str) -> list[dict[str, Any]]:
    """Jobs that use `raster_id` as an *input*. Each: {job_id, algorithm, status, role}."""
    refs: list[dict[str, Any]] = []
    with connect() as c:
        rows = c.execute("SELECT id, algorithm, status, inputs FROM jobs").fetchall()
    for row in rows:
        for role, rid in json.loads(row["inputs"]).items():
            if rid == raster_id:
                refs.append({
                    "job_id": row["id"],
                    "algorithm": row["algorithm"],
                    "status": row["status"],
                    "role": role,
                })
    return refs


def delete_raster(raster_id: str) -> Optional[str]:
    """Delete raster record from DB; return cog_path if it existed, else None.

    Jobs that produced this raster get `output_id` cleared so they never point
    at a missing row.
    """
    with connect() as c:
        row = c.execute("SELECT cog_path FROM rasters WHERE id = ?", (raster_id,)).fetchone()
        if row is None:
            return None
        cog_path = row["cog_path"]
        c.execute("UPDATE jobs SET output_id = NULL WHERE output_id = ?", (raster_id,))
        c.execute("DELETE FROM rasters WHERE id = ?", (raster_id,))
    return cog_path
