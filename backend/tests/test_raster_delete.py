"""DELETE /rasters/{id} reference-check behaviour."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(isolated_data_dirs, monkeypatch):
    from app.jobs import runner
    monkeypatch.setattr(runner, "submit", lambda *a, **kw: None)
    from app.main import app
    return TestClient(app)


def _raster(store, name, kind="source"):
    return store.insert_raster(
        name=name, cog_path=Path(f"/tmp/{name}-nonexistent.tif"), crs="EPSG:32650",
        bounds=(0.0, 0.0, 1.0, 1.0), bounds_wgs84=(0.0, 0.0, 1.0, 1.0),
        resolution=(1.0, 1.0), width=10, height=10, dtype="float32", nodata=None,
        kind=kind,
    )


def test_unreferenced_delete_ok(client):
    from app.storage import local as store
    rid = _raster(store, "a")
    assert client.delete(f"/rasters/{rid}").status_code == 204
    assert store.get_raster(rid) is None


def test_delete_missing_404(client):
    assert client.delete("/rasters/nope").status_code == 404


def test_active_job_blocks_even_with_force(client):
    from app.storage import local as store
    rid = _raster(store, "a")
    store.insert_job("slope", {}, {"dem": rid})  # pending
    for q in ("", "?force=true"):
        r = client.delete(f"/rasters/{rid}{q}")
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "in_use"
    assert store.get_raster(rid) is not None


def test_finished_job_requires_force(client):
    from app.storage import local as store
    rid = _raster(store, "a")
    jid = store.insert_job("slope", {}, {"dem": rid})
    store.update_job(jid, status="succeeded")
    r = client.delete(f"/rasters/{rid}")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "referenced"
    assert detail["jobs"][0]["job_id"] == jid
    assert client.delete(f"/rasters/{rid}?force=true").status_code == 204
    assert store.get_raster(rid) is None
    assert store.get_job(jid)["inputs"] == {"dem": rid}  # history kept


def test_deleting_result_clears_output_id(client):
    from app.storage import local as store
    src = _raster(store, "src")
    out = _raster(store, "out", kind="result")
    jid = store.insert_job("slope", {}, {"dem": src})
    store.update_job(jid, status="succeeded", output_id=out)
    assert client.delete(f"/rasters/{out}").status_code == 204
    assert store.get_job(jid)["output_id"] is None
