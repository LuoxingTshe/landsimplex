"""Incremental SQLite schema migrations. Called from init_db() on every startup.

Each helper checks before altering, so re-running is always safe.
"""
from __future__ import annotations

import sqlite3


def run_migrations(conn: sqlite3.Connection) -> None:
    _add_column_if_missing(conn, "rasters", "scene_id",     "TEXT")
    _add_column_if_missing(conn, "rasters", "sensor",       "TEXT")
    _add_column_if_missing(conn, "rasters", "band_id",      "TEXT")
    _add_column_if_missing(conn, "rasters", "auto_role",    "TEXT")
    _add_column_if_missing(conn, "rasters", "role",         "TEXT")
    _add_column_if_missing(conn, "rasters", "resolution_m", "INTEGER")
    _add_column_if_missing(conn, "rasters", "stats_min",    "REAL")
    _add_column_if_missing(conn, "rasters", "stats_max",    "REAL")
    _create_scenes_table(conn)
    # Parameter sweep support — see backend/app/jobs/sweep.py.
    # All new columns nullable so existing rows read as plain single jobs.
    _add_column_if_missing(conn, "jobs", "kind",         "TEXT NOT NULL DEFAULT 'single'")
    _add_column_if_missing(conn, "jobs", "parent_id",    "TEXT")
    _add_column_if_missing(conn, "jobs", "param_ranges", "TEXT")
    _add_column_if_missing(conn, "jobs", "sample_count", "INTEGER")
    _add_column_if_missing(conn, "jobs", "sample_label", "TEXT")
    _add_column_if_missing(conn, "jobs", "sample_index", "INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs(parent_id)")


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _create_scenes_table(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scenes (
        scene_id    TEXT PRIMARY KEY,
        sensor      TEXT NOT NULL,
        acquired_at TEXT,
        raw_name    TEXT NOT NULL,
        created_at  TEXT NOT NULL
    );
    """)
