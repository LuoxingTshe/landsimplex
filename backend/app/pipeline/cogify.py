"""COG-ification: convert any input GeoTIFF into a Cloud Optimized GeoTIFF
with internal overviews. This is the single normalization at import time.

The resulting COG is the canonical storage form. rio-tiler reads from it for
the tile endpoint, and the pipeline reads from it for algorithms.

Large-file path
---------------
GDAL's COG driver with overviews="AUTO" computes overviews internally using
/vsimem/ (virtual RAM).  For files whose uncompressed size exceeds
_LARGE_FILE_BYTES this causes OOM.  We avoid it with a two-step approach:

  1. Write an intermediate tiled GeoTIFF to disk (no overviews yet).
     GDAL streams data in its own tile buffer — Python never holds the full
     array.  Peak RAM ≈ GDAL_CACHEMAX (capped at 512 MiB).
  2. Build overviews in-place on that file.  GDAL reads level-by-level from
     the tiled source; peak RAM ≈ GDAL_CACHEMAX.
  3. Write final COG with copy_src_overviews=True.  GDAL copies pre-built
     overviews without recomputing; no /vsimem/ allocation needed.

Peak disk during conversion ≈ 2.7× uncompressed source size (intermediate
tiled GTiff ≈ source, final COG ≈ source + 1/3 for overviews, then both
temporary files are removed).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy


def _repair_local_cs(crs: rasterio.crs.CRS) -> rasterio.crs.CRS | None:
    """If crs is a LOCAL_CS without datum, try to resolve it via pyproj name lookup.

    Returns the repaired CRS, or None if the CRS is already valid or can't be repaired.
    """
    if crs.is_projected or crs.is_geographic:
        return None
    # LOCAL_CS — extract the name from the WKT (first quoted token) and query pyproj.
    # crs.to_string() returns the raw WKT which pyproj re-parses as ENGCRS (still no
    # datum), so we must use the human-readable name (e.g. "CH1903+ / LV95") directly.
    import re
    from pyproj import CRS as ProjCRS

    wkt = crs.to_wkt()
    m = re.search(r'"([^"]+)"', wkt)
    if not m:
        return None
    name = m.group(1)
    try:
        resolved = ProjCRS.from_user_input(name)
        if resolved.is_projected or resolved.is_geographic:
            return rasterio.crs.CRS.from_wkt(resolved.to_wkt())
    except Exception:
        pass
    return None

# Standard 512px tile size for COG. rio-tiler's defaults align well with this.
COG_TILE_SIZE = 512

# Overview levels used both for COG creation and algorithm output writing.
OVERVIEW_LEVELS = [2, 4, 8, 16, 32, 64]

# Uncompressed bytes above which we use the two-step large-file path.
_LARGE_FILE_BYTES = 200 * 1024 * 1024  # 200 MiB

# GDAL block-cache cap (MiB) applied during large-file operations.
_GDAL_CACHE_MIB = 512


def cogify(src_path: Path, dst_path: Path) -> dict:
    """Read src_path, write a COG with overviews to dst_path.

    Returns a dict of metadata extracted from the source for the catalogue.

    Raises ValueError if the source isn't suitable (no CRS, not north-up, etc.).
    """
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError("Source raster has no CRS. Cannot import.")
        if src.transform.e >= 0:
            raise ValueError("Source raster is not north-up. Reproject before importing.")
        if src.count < 1:
            raise ValueError("Source raster has zero bands.")

        repaired_crs = _repair_local_cs(src.crs)
        effective_crs = repaired_crs if repaired_crs is not None else src.crs

        meta = {
            "crs": effective_crs.to_string(),
            "crs_wkt": effective_crs.to_wkt(),
            "bounds": tuple(src.bounds),
            "transform": src.transform,
            "width": src.width,
            "height": src.height,
            "count": src.count,
            "dtype": src.dtypes[0],
            "nodata": src.nodata,
            "resolution": (src.transform.a, -src.transform.e),
        }

        # predictor=2 (horizontal differencing) improves deflate compression for
        # integer rasters (signed and unsigned, including uint16 Sentinel-2 data).
        dtype_kind = src.dtypes[0]
        use_predictor = 2 if dtype_kind not in ("float32", "float64") else 1
        n_bytes = src.width * src.height * src.count * np.dtype(dtype_kind).itemsize

    # If the source has a LOCAL_CS that was resolved, we need to fix the CRS before
    # cogifying. Patching a COG in place breaks its layout, so instead we write a
    # temporary GTiff copy of the source with the corrected CRS and use that as input.
    if repaired_crs is not None:
        crs_fixed_src = dst_path.with_name(dst_path.stem + "._crs_fix.tif")
        shutil.copy2(src_path, crs_fixed_src)
        try:
            with rasterio.open(crs_fixed_src, "r+") as ds:
                ds.crs = repaired_crs
            effective_src = crs_fixed_src
        except Exception:
            crs_fixed_src.unlink(missing_ok=True)
            effective_src = src_path
    else:
        effective_src = src_path
        crs_fixed_src = None

    try:
        if n_bytes <= _LARGE_FILE_BYTES:
            # Small file: single-pass COG conversion.  GDAL's vsimem overhead is
            # acceptable (≤ 200 MiB per band).
            rio_copy(
                effective_src,
                dst_path,
                driver="COG",
                blocksize=COG_TILE_SIZE,
                compress="deflate",
                predictor=use_predictor,
                overview_resampling="average",
                overviews="AUTO",
            )
        else:
            _cogify_large(effective_src, dst_path, use_predictor)
    finally:
        if crs_fixed_src is not None:
            crs_fixed_src.unlink(missing_ok=True)

    return meta


def _cogify_large(src_path: Path, dst_path: Path, predictor: int) -> None:
    """Two-step, memory-bounded COG creation for large rasters.

    All three steps run inside a rasterio.Env that caps GDAL's block cache at
    _GDAL_CACHE_MIB and sets the overview tile size to match COG_TILE_SIZE.
    """
    tmp_tif = dst_path.with_suffix(".ovr_stage.tif")
    try:
        with rasterio.Env(
            GDAL_CACHEMAX=_GDAL_CACHE_MIB,
            GDAL_TIFF_OVR_BLOCKSIZE=COG_TILE_SIZE,
        ):
            # Step 1 — tiled GeoTIFF on disk (no overviews).
            # GDAL streams in its tile buffer; Python never holds the full array.
            rio_copy(
                src_path,
                tmp_tif,
                driver="GTiff",
                tiled=True,
                blockxsize=COG_TILE_SIZE,
                blockysize=COG_TILE_SIZE,
                compress="deflate",
                predictor=predictor,
            )

            # Step 2 — build overviews in-place; GDAL reads level-by-level.
            with rasterio.open(tmp_tif, "r+") as ds:
                ds.build_overviews(OVERVIEW_LEVELS, Resampling.average)
                ds.update_tags(ns="rio_overview", resampling="average")

            # Step 3 — copy to final COG reusing pre-built overviews.
            # copy_src_overviews=True tells GDAL to skip internal recomputation,
            # so no /vsimem/ allocation is needed.
            rio_copy(
                tmp_tif,
                dst_path,
                driver="COG",
                blocksize=COG_TILE_SIZE,
                compress="deflate",
                predictor=predictor,
                copy_src_overviews=True,
            )
    finally:
        tmp_tif.unlink(missing_ok=True)
