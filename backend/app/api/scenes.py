"""Scenes API: upload satellite scene bundles, list scenes, override band roles."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

import rasterio
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from rasterio.warp import transform_bounds

from ..config import BUNDLE_STAGING_DIR, COG_DIR, UPLOAD_TMP
from ..pipeline.cogify import cogify
from ..pipeline.ingest import (
    VALID_ROLES,
    BandFile,
    detect_bundle_type,
    detect_landsat_sensor,
    discover_landsat_bands,
    discover_sentinel2_bands,
    extract_bundle,
    make_staging_path,
    parse_acquisition_time,
    parse_landsat_scene_id,
    parse_sentinel2_scene_id,
)
from ..storage import local as store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/scenes", tags=["scenes"])


class SetRoleBody(BaseModel):
    role: str


@router.post("/upload")
async def upload_scene(file: UploadFile = File(...)) -> dict:
    """Upload a Sentinel-2 .zip (SAFE) or Landsat 8/9 .tar.gz bundle.

    Extracts each band, converts to COG, and catalogues with sensor/band metadata.
    """
    filename = file.filename or "bundle"
    try:
        sensor_type = detect_bundle_type(Path(filename))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    # Stage the uploaded archive to a unique temp path.
    upload_dir = UPLOAD_TMP / make_staging_path(Path(".")).name
    upload_dir.mkdir(parents=True, exist_ok=True)
    archive_path = upload_dir / filename
    try:
        with archive_path.open("wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)

        # Extract archive.
        staging_path = make_staging_path(BUNDLE_STAGING_DIR)
        try:
            extracted_root = extract_bundle(archive_path, staging_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        try:
            return _ingest_scene(extracted_root, sensor_type, filename)
        finally:
            shutil.rmtree(staging_path, ignore_errors=True)
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)


def _ingest_scene(extracted_root: Path, sensor_type: str, raw_name: str) -> dict:
    # Discover bands and derive scene metadata.
    if sensor_type == "sentinel2":
        bands = discover_sentinel2_bands(extracted_root)
        scene_id = parse_sentinel2_scene_id(extracted_root)
        sensor = "sentinel2"
    else:
        bands = discover_landsat_bands(extracted_root)
        scene_id = parse_landsat_scene_id(extracted_root)
        sensor = detect_landsat_sensor(extracted_root)

    if not bands:
        raise HTTPException(400, "No recognisable band files found in archive.")

    if store.scene_exists(scene_id):
        raise HTTPException(409, f"Scene already ingested: {scene_id}")

    acquired_at = parse_acquisition_time(extracted_root, sensor_type)
    store.insert_scene(scene_id, sensor, acquired_at, raw_name)

    ingested: list[dict] = []
    failed: list[dict] = []

    for band in bands:
        cog_name = f"{scene_id}_{band.band_id}.tif"
        cog_path = COG_DIR / cog_name
        try:
            meta = cogify(band.path, cog_path)
        except Exception as exc:
            log.warning("cogify failed for %s band %s: %s", scene_id, band.band_id, exc)
            failed.append({"band_id": band.band_id, "error": str(exc)})
            continue

        with rasterio.open(cog_path) as ds:
            wgs_bounds = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)

        raster_id = store.insert_raster(
            name=f"{scene_id}_{band.band_id}",
            cog_path=cog_path,
            crs=meta["crs"],
            bounds=meta["bounds"],
            bounds_wgs84=wgs_bounds,
            resolution=meta["resolution"],
            width=meta["width"],
            height=meta["height"],
            dtype=meta["dtype"],
            nodata=meta["nodata"],
            kind="source",
            semantic="continuous",
            scene_id=scene_id,
            sensor=sensor,
            band_id=band.band_id,
            auto_role=band.auto_role,
            role=band.auto_role,
            resolution_m=band.resolution_m,
        )
        ingested.append(store.get_raster(raster_id))

    scene = store.get_scene(scene_id)
    scene["failed_bands"] = failed
    return scene


@router.get("")
def list_scenes() -> list[dict]:
    return store.list_scenes()


@router.get("/{scene_id}")
def get_scene(scene_id: str) -> dict:
    scene = store.get_scene(scene_id)
    if scene is None:
        raise HTTPException(404, f"Scene {scene_id!r} not found")
    scene.setdefault("failed_bands", [])
    return scene


@router.patch("/bands/{raster_id}/role")
def set_band_role(raster_id: str, body: SetRoleBody) -> dict:
    if body.role not in VALID_ROLES:
        raise HTTPException(400, f"Unknown role {body.role!r}. Valid roles: {sorted(VALID_ROLES)}")
    r = store.get_raster(raster_id)
    if r is None:
        raise HTTPException(404, f"Raster {raster_id!r} not found")
    store.update_raster_role(raster_id, body.role)
    return {"id": raster_id, "role": body.role, "auto_role": r.get("auto_role")}
