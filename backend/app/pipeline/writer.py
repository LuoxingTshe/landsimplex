"""Write a single array from an AlignedStack to a COG.

Used by algorithm runners to persist outputs into the same canonical storage
form (COG with overviews) as imported sources.

The write is always done window-by-window so that:
  - no full-sized temporary copy is created for the nodata masking step
  - the tiled intermediate GTiff accumulates data without a full in-RAM buffer

Peak Python RAM per write ≈ one _TILE_SIZE² chunk ≈ 1 MB.

Large-output path
-----------------
When the output array exceeds _LARGE_OUTPUT_BYTES we apply the same two-step
COG finalisation used in cogify._cogify_large: build overviews on the tiled
intermediate file before calling rio_copy with copy_src_overviews=True.  This
avoids GDAL's /vsimem/ allocation for overview computation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy

from .cogify import COG_TILE_SIZE, OVERVIEW_LEVELS, _GDAL_CACHE_MIB
from .stack import AlignedStack

_TILE_SIZE = COG_TILE_SIZE
_LARGE_OUTPUT_BYTES = 200 * 1024 * 1024  # 200 MiB


def write_stack_array(
    stack: AlignedStack,
    array_name: str,
    dst_path: Path,
    *,
    nodata: float = float("nan"),
    dtype: str = "float32",
) -> None:
    """Write `stack.arrays[array_name]` to `dst_path` as a COG."""
    arr = stack[array_name]
    spec = stack.spec

    profile = {
        "driver": "GTiff",
        "height": spec.height,
        "width": spec.width,
        "count": 1,
        "dtype": dtype,
        "crs": spec.crs,
        "transform": spec.transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": _TILE_SIZE,
        "blockysize": _TILE_SIZE,
        "compress": "deflate",
    }

    tmp_path = dst_path.with_suffix(".tmp.tif")
    try:
        # Step 1 — write the tiled intermediate GTiff window-by-window.
        with rasterio.open(tmp_path, "w", **profile) as dst:
            for _, window in dst.block_windows(1):
                row_off = int(window.row_off)
                col_off = int(window.col_off)
                h = int(window.height)
                w = int(window.width)

                tile = np.array(arr[row_off : row_off + h, col_off : col_off + w], dtype=dtype)
                if stack.mask is not None and dtype.startswith("float"):
                    mask_tile = stack.mask[row_off : row_off + h, col_off : col_off + w]
                    tile[~mask_tile] = nodata
                dst.write(tile, 1, window=window)

        n_bytes = spec.height * spec.width * np.dtype(dtype).itemsize
        if n_bytes <= _LARGE_OUTPUT_BYTES:
            # Small output: single-pass COG conversion.
            rio_copy(
                tmp_path,
                dst_path,
                driver="COG",
                blocksize=COG_TILE_SIZE,
                compress="deflate",
                overview_resampling="average",
            )
        else:
            # Large output: build overviews first to avoid /vsimem/ OOM.
            with rasterio.Env(
                GDAL_CACHEMAX=_GDAL_CACHE_MIB,
                GDAL_TIFF_OVR_BLOCKSIZE=COG_TILE_SIZE,
            ):
                with rasterio.open(tmp_path, "r+") as ds:
                    ds.build_overviews(OVERVIEW_LEVELS, Resampling.average)
                    ds.update_tags(ns="rio_overview", resampling="average")
                rio_copy(
                    tmp_path,
                    dst_path,
                    driver="COG",
                    blocksize=COG_TILE_SIZE,
                    compress="deflate",
                    copy_src_overviews=True,
                )
    finally:
        tmp_path.unlink(missing_ok=True)
