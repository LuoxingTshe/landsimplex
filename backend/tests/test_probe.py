"""Tests for GET /probe/{raster_id} — pixel layer values + weight-space lattice.

Runs the real runner pipeline (normalize → algorithm → COG writer) in-process
on small synthetic GeoTIFFs, so the probe's lattice pass rate can be checked
against the value actually written to the result raster.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import rasterio
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_transform

H = W = 16
CRS = "EPSG:32650"
# Origin on normalize()'s snap grid (multiples of the 30 m resolution), so the
# result grid equals the input grid and raw pixel values can be compared.
TRANSFORM = from_origin(500010.0, 3000000.0, 30.0, 30.0)
NODATA_PIXEL = (3, 3)  # nodata in layer_2


@pytest.fixture
def env(isolated_data_dirs, tmp_path, monkeypatch):
    return _make_env(isolated_data_dirs, tmp_path, monkeypatch, TRANSFORM)


def _make_env(isolated_data_dirs, tmp_path, monkeypatch, transform):
    from app.jobs import runner
    monkeypatch.setattr(runner, "RESULT_DIR", isolated_data_dirs.RESULT_DIR)
    monkeypatch.setattr(runner, "submit", lambda *a, **kw: None)
    from app.main import app
    from app.storage import local as store

    rng = np.random.default_rng(7)
    layers = [rng.random((H, W), dtype=np.float32) for _ in range(3)]
    layers[2][NODATA_PIXEL] = -9999.0
    ids = []
    for i, arr in enumerate(layers):
        path = tmp_path / f"layer{i}.tif"
        with rasterio.open(
            path, "w", driver="GTiff", height=H, width=W, count=1, dtype="float32",
            crs=CRS, transform=transform, nodata=-9999.0,
        ) as dst:
            dst.write(arr, 1)
        ids.append(store.insert_raster(
            name=f"layer{i}", cog_path=path, crs=CRS,
            bounds=(transform.c, transform.f - 30 * H, transform.c + 30 * W, transform.f),
            bounds_wgs84=(0, 0, 1, 1), resolution=(30.0, 30.0),
            width=W, height=H, dtype="float32", nodata=-9999.0,
        ))
    inputs = {f"layer_{i}": rid for i, rid in enumerate(ids)}
    return TestClient(app), store, runner, inputs, layers


def _lonlat(row: int, col: int, transform=TRANSFORM) -> tuple[float, float]:
    x, y = transform @ (col + 0.5, row + 0.5)
    lon, lat = warp_transform(CRS, "EPSG:4326", [x], [y])
    return lon[0], lat[0]


def _run(store, runner, algorithm, params, inputs, **kw) -> str:
    job_id = store.insert_job(algorithm, params, inputs, **kw)
    runner._run_job(job_id, algorithm, params, inputs)
    job = store.get_job(job_id)
    assert job["status"] == "succeeded", job["message"]
    return job["output_id"]


def _probe(client, raster_id, row, col):
    lon, lat = _lonlat(row, col)
    return client.get(f"/probe/{raster_id}", params={"lon": lon, "lat": lat})


def _pass_rate(body) -> float:
    lattice = np.asarray(body["lattice"])
    scores = lattice @ np.asarray(body["values"])
    return float(np.mean(scores > body["threshold"]))


# ---------- threshold_probability ----------


@pytest.mark.parametrize("bounds", [{}, {"min": [0.1, 0.0, 0.2], "max": [0.6, 0.7, 0.9]}])
def test_threshold_probability_pass_rate_matches_map(env, bounds):
    client, store, runner, inputs, layers = env
    params = {
        "target_score": 0.5,
        "weights": [1 / 3] * 3,
        "_internal_sweep": {"weights": {"kind": "simplex", "n_divisions": 6, **bounds}},
    }
    out_id = _run(store, runner, "threshold_probability", params, inputs)

    for row, col in [(0, 0), (5, 9), (10, 2), (15, 15), (8, 8)]:
        r = _probe(client, out_id, row, col)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "threshold_probability"
        assert body["pixel"] == {**body["pixel"], "row": row, "col": col}
        assert len(body["pixel"]["footprint"]) == 4
        assert body["valid"] is True
        # Same grid as the inputs → sampled values are the raw pixel values.
        np.testing.assert_allclose(body["values"], [l[row, col] for l in layers], rtol=1e-6)
        assert _pass_rate(body) == pytest.approx(body["output_value"], abs=1e-5)


def test_misaligned_grid_pass_rate_still_matches_map(isolated_data_dirs, tmp_path, monkeypatch):
    """Off-snap origin: the result grid is shifted half a pixel and inputs are
    bilinear-resampled. The probe must sample the same warped values."""
    shifted = from_origin(500015.0, 3000015.0, 30.0, 30.0)
    client, store, runner, inputs, _ = _make_env(isolated_data_dirs, tmp_path, monkeypatch, shifted)
    params = {"target_score": 0.5, "weights": [1 / 3] * 3,
              "_internal_sweep": {"weights": {"kind": "simplex", "n_divisions": 8}}}
    out_id = _run(store, runner, "threshold_probability", params, inputs)
    checked = 0
    for row, col in [(1, 1), (6, 9), (12, 4), (14, 13)]:
        lon, lat = _lonlat(row, col, shifted)
        body = client.get(f"/probe/{out_id}", params={"lon": lon, "lat": lat}).json()
        if body["valid"]:
            assert _pass_rate(body) == pytest.approx(body["output_value"], abs=1e-5)
            checked += 1
    assert checked >= 3


def test_bounds_filter_lattice(env):
    client, store, runner, inputs, _ = env
    spec = {"kind": "simplex", "n_divisions": 4, "min": [0.25, 0.0, 0.0], "max": [1.0, 0.5, 1.0]}
    params = {"target_score": 0.5, "weights": [1 / 3] * 3, "_internal_sweep": {"weights": spec}}
    out_id = _run(store, runner, "threshold_probability", params, inputs)
    body = _probe(client, out_id, 1, 1).json()
    lattice = np.asarray(body["lattice"])
    assert np.all(lattice[:, 0] >= 0.25 - 1e-9) and np.all(lattice[:, 1] <= 0.5 + 1e-9)
    assert body["min"] == [0.25, 0.0, 0.0] and body["n_divisions"] == 4


def test_nodata_pixel_is_invalid(env):
    client, store, runner, inputs, _ = env
    params = {"target_score": 0.5, "weights": [1 / 3] * 3,
              "_internal_sweep": {"weights": {"kind": "simplex", "n_divisions": 3}}}
    out_id = _run(store, runner, "threshold_probability", params, inputs)
    body = _probe(client, out_id, *NODATA_PIXEL).json()
    assert body["valid"] is False
    assert body["values"][2] is None


def test_outside_raster_404(env):
    client, store, runner, inputs, _ = env
    params = {"target_score": 0.5, "weights": [1 / 3] * 3,
              "_internal_sweep": {"weights": {"kind": "simplex", "n_divisions": 3}}}
    out_id = _run(store, runner, "threshold_probability", params, inputs)
    lon, lat = _lonlat(0, 0)
    r = client.get(f"/probe/{out_id}", params={"lon": lon - 1.0, "lat": lat})
    assert r.status_code == 404


# ---------- weighted_overlay sweep ----------


def test_overlay_sweep_child_maps_to_parent_lattice(env):
    client, store, runner, inputs, _ = env
    ranges = {"weights": {"kind": "simplex", "n_divisions": 4}}
    parent_id = store.insert_sweep_job(
        "weighted_overlay", {"weights": [1 / 3] * 3}, ranges, inputs, sample_count=15
    )
    child_w = [0.5, 0.25, 0.25]
    out_id = _run(store, runner, "weighted_overlay", {"weights": child_w}, inputs,
                  kind="sweep_child", parent_id=parent_id, sample_label="w", sample_index=0)

    body = _probe(client, out_id, 6, 11).json()
    assert body["mode"] == "overlay_sweep"
    assert body["threshold"] is None
    assert len(body["lattice"]) == 15  # C(3+4-1, 4)
    # The child's own score at this pixel is reproduced by w·v.
    assert float(np.dot(child_w, body["values"])) == pytest.approx(body["output_value"], abs=1e-6)


# ---------- not probe-able ----------


def test_single_overlay_result_not_probeable(env):
    client, store, runner, inputs, _ = env
    out_id = _run(store, runner, "weighted_overlay", {"weights": [0.2, 0.3, 0.5]}, inputs)
    r = _probe(client, out_id, 0, 0)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_probeable"


def test_source_raster_not_probeable(env):
    client, _, _, inputs, _ = env
    r = _probe(client, inputs["layer_0"], 0, 0)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "not_probeable"


def test_unknown_raster_404(env):
    client = env[0]
    assert client.get("/probe/nope", params={"lon": 0, "lat": 0}).status_code == 404
