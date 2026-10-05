"""Integration tests for the jobs API — single + sweep paths.

The runner is replaced with a no-op so submissions exercise validation,
storage, and routing without spinning up a ProcessPoolExecutor or touching
real rasters.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(isolated_data_dirs, monkeypatch):
    """FastAPI client with isolated data dirs and a stubbed runner."""
    # Stub the runner so jobs stay in 'pending' — we only assert routing/storage.
    from app.jobs import runner
    monkeypatch.setattr(runner, "submit", lambda *a, **kw: None)

    from app.main import app
    return TestClient(app)


def _make_raster(store, name: str) -> str:
    """Insert a fake raster row so input-existence checks pass."""
    return store.insert_raster(
        name=name,
        cog_path=Path(f"/tmp/{name}.tif"),
        crs="EPSG:4326",
        bounds=(0.0, 0.0, 1.0, 1.0),
        bounds_wgs84=(0.0, 0.0, 1.0, 1.0),
        resolution=(0.01, 0.01),
        width=100, height=100, dtype="float32",
        nodata=None,
    )


# ---------- single-job path (regression) ----------

def test_submit_without_param_ranges_returns_single_job(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.3, 0.7]},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "single"
    assert body["status"] == "pending"
    assert "child_ids" not in body
    assert body["params"] == {"weights": [0.3, 0.7]}


def test_submit_explicit_null_param_ranges_treated_as_single(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [1.0, 1.0]},
        "param_ranges": None,
    })
    assert r.status_code == 200
    assert r.json()["kind"] == "single"


# ---------- sweep path ----------

def test_sweep_weighted_overlay_simplex(app_client):
    """Simplex sweep with n=2, T=5 produces C(6,5)=6 samples."""
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {"kind": "simplex", "n_divisions": 5}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "sweep"
    assert body["sample_count"] == 6
    assert len(body["child_ids"]) == 6
    assert body["output_id"] is None

    # GET /jobs/{id}/children returns 6 distinct labels.
    r2 = app_client.get(f"/jobs/{body['id']}/children")
    assert r2.status_code == 200
    kids = r2.json()
    assert len(kids) == 6
    labels = [k["sample_label"] for k in kids]
    assert len(set(labels)) == 6
    # Each child has weights summing to 1.
    for k in kids:
        w = k["params"]["weights"]
        assert abs(sum(w) - 1.0) < 1e-6
    assert all(k["kind"] == "sweep_child" for k in kids)
    assert all(k["parent_id"] == body["id"] for k in kids)


def test_sweep_empty_param_ranges_falls_through_to_single(app_client):
    """Empty param_ranges dict collapses to a normal single job."""
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "single"
    assert "child_ids" not in body


def test_sweep_over_cap_returns_400(app_client):
    """Simplex n=2, T=201 → C(202,201)=202 > 200 → 400."""
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {"kind": "simplex", "n_divisions": 201}},
    })
    assert r.status_code == 400
    assert "exceeds cap" in r.json()["detail"]


def test_sweep_with_invalid_n_divisions_returns_400(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {"kind": "simplex", "n_divisions": 0}},
    })
    assert r.status_code == 400
    assert "n_divisions" in r.json()["detail"]


def test_children_endpoint_404_for_unknown_job(app_client):
    r = app_client.get("/jobs/nonexistent/children")
    assert r.status_code == 404


def test_sweep_with_bounds_filters_children(app_client):
    """n=3, T=4 unfiltered is 15; max=[0.5,0.5,1.0] drops some samples."""
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    c = _make_raster(store, "c")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b, "layer_2": c},
        "params": {"weights": [0.33, 0.33, 0.34]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "max": [0.5, 0.5, 1.0],
        }},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "sweep"
    # All survivors must respect the bounds.
    kids = app_client.get(f"/jobs/{body['id']}/children").json()
    assert len(kids) == body["sample_count"]
    assert len(kids) < 15
    for k in kids:
        w = k["params"]["weights"]
        assert w[0] <= 0.5 + 1e-9
        assert w[1] <= 0.5 + 1e-9


def test_sweep_bounds_single_survivor_uses_resolved_params(app_client):
    """Bounds tight enough to leave exactly one lattice point: the resulting
    single job must use that survivor's weights, not the base params."""
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    c = _make_raster(store, "c")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b, "layer_2": c},
        "params": {"weights": [0.33, 0.33, 0.34]},  # base — must NOT be used
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.45, 0.45, 0.0],
            "max": [0.55, 0.55, 1.0],
        }},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "single"
    # The lone T=2 survivor of these bounds is (0.5, 0.5, 0.0).
    assert body["params"]["weights"] == [0.5, 0.5, 0.0]


def test_sweep_bounds_eliminate_all_returns_400(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    c = _make_raster(store, "c")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b, "layer_2": c},
        "params": {"weights": [0.33, 0.33, 0.34]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.45, 0.45, 0.0],
            "max": [0.49, 0.55, 1.0],
        }},
    })
    assert r.status_code == 400
    assert "eliminate" in r.json()["detail"]


def test_sweep_bounds_validation_error_returns_400(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "max": [1.5, 1.0],
        }},
    })
    assert r.status_code == 400
    assert "[0, 1]" in r.json()["detail"]


# ---------- /jobs/preview ----------

def test_preview_returns_simplex_count(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "weighted_overlay",
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {"kind": "simplex", "n_divisions": 5}},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sample_count"] == 6  # C(2+5-1, 5) = 6
    assert body["max_samples"] == 200


def test_preview_returns_one_without_ranges(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "weighted_overlay",
        "params": {"weights": [0.5, 0.5]},
    })
    assert r.status_code == 200
    assert r.json()["sample_count"] == 1


def test_preview_returns_count_with_bounds(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "weighted_overlay",
        "params": {"weights": [0.33, 0.33, 0.34]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "max": [0.5, 0.5, 1.0],
        }},
    })
    assert r.status_code == 200
    n = r.json()["sample_count"]
    assert 1 <= n < 15


def test_preview_400_when_bounds_eliminate_all(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "weighted_overlay",
        "params": {"weights": [0.33, 0.33, 0.34]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.45, 0.45, 0.0],
            "max": [0.49, 0.55, 1.0],
        }},
    })
    assert r.status_code == 400
    assert "eliminate" in r.json()["detail"]


def test_preview_400_for_invalid_bounds(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "weighted_overlay",
        "params": {"weights": [0.5, 0.5]},
        "param_ranges": {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "min": [0.7, 0.7],  # sum > 1
        }},
    })
    assert r.status_code == 400


def test_preview_404_for_unknown_algorithm(app_client):
    r = app_client.post("/jobs/preview", json={
        "algorithm": "nonexistent",
        "params": {},
        "param_ranges": {"weights": {"kind": "simplex", "n_divisions": 4}},
    })
    assert r.status_code == 404


def test_children_endpoint_400_for_single_job(app_client):
    from app.storage import local as store
    a = _make_raster(store, "a")
    b = _make_raster(store, "b")
    r = app_client.post("/jobs", json={
        "algorithm": "weighted_overlay",
        "inputs": {"layer_0": a, "layer_1": b},
        "params": {"weights": [0.5, 0.5]},
    })
    job_id = r.json()["id"]
    r2 = app_client.get(f"/jobs/{job_id}/children")
    assert r2.status_code == 400
    assert "not a sweep" in r2.json()["detail"]
