"""Rasters API: upload, list, fetch metadata, delete."""
from __future__ import annotations

from pathlib import Path

import rasterio
from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from rasterio.warp import transform_bounds

from ..config import COG_DIR, UPLOAD_TMP
from ..pipeline.cogify import cogify
from ..storage import local as store

router = APIRouter(prefix="/rasters", tags=["rasters"])


@router.get("")
def list_rasters() -> list[dict]:
    return store.list_rasters()


@router.get("/{raster_id}")
def get_raster(raster_id: str) -> dict:
    r = store.get_raster(raster_id)
    if r is None:
        raise HTTPException(404, f"Raster {raster_id} not found")
    return r


@router.post("/upload")
async def upload_raster(file: UploadFile = File(...)) -> dict:
    """Upload a GeoTIFF, convert to COG, register in catalogue."""
    if not file.filename or not file.filename.lower().endswith((".tif", ".tiff")):
        raise HTTPException(400, "Only .tif/.tiff files accepted in MVP")

    # Save upload to a temp file (rasterio needs a path)
    tmp_path = UPLOAD_TMP / file.filename
    with tmp_path.open("wb") as f:
        while chunk := await file.read(1 << 20):  # 1 MB chunks
            f.write(chunk)

    # COGify into the canonical location
    cog_path = COG_DIR / f"{tmp_path.stem}.tif"
    try:
        meta = cogify(tmp_path, cog_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, f"COGify failed: {exc}")

    # Re-read the COG to get final metadata + WGS84 bounds for map fit
    with rasterio.open(cog_path) as ds:
        try:
            wgs_bounds = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
        except Exception as exc:
            cog_path.unlink(missing_ok=True)
            raise HTTPException(
                400,
                f"Cannot compute WGS84 bounds — CRS has no datum transformation: {exc}. "
                "Re-export the file with a standard EPSG CRS.",
            )

    raster_id = store.insert_raster(
        name=tmp_path.stem,
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
    )

    tmp_path.unlink(missing_ok=True)
    return store.get_raster(raster_id)


_ACTIVE_STATUSES = ("pending", "running")


@router.delete("/{raster_id}")
def delete_raster(raster_id: str, force: bool = False) -> Response:
    """Remove a raster from the catalogue and delete its COG file.

    Refuses (409) when a pending/running job uses it as input, and — unless
    `force=true` — when finished jobs reference it. See CLAUDE.md.
    """
    if store.get_raster(raster_id) is None:
        raise HTTPException(404, f"Raster {raster_id} not found")

    refs = store.find_raster_references(raster_id)
    active = [r for r in refs if r["status"] in _ACTIVE_STATUSES]
    if active:
        raise HTTPException(409, {
            "code": "in_use",
            "message": "Raster is an input of a pending/running job",
            "jobs": active,
        })
    if refs and not force:
        raise HTTPException(409, {
            "code": "referenced",
            "message": "Raster is an input of existing jobs; retry with force=true to delete anyway",
            "jobs": refs,
        })

    cog_path = store.delete_raster(raster_id)
    if cog_path:
        Path(cog_path).unlink(missing_ok=True)
    return Response(status_code=204)
