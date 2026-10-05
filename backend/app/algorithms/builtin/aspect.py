"""Aspect (degrees, 0-360) via GDAL's DEMProcessing (gdaldem aspect).

Input:  one continuous raster named "dem" (elevation in metres)
Output: aspect in degrees 0-360, clockwise from north, same grid as input.
        Flat areas receive NaN.

Large-raster tiling mirrors slope.py: tiles of _TILE x _TILE pixels with a
1-pixel halo so GDAL has neighbour context at tile borders.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from osgeo import gdal

from ...pipeline.stack import AlignedStack
from ..registry import AlgorithmInfo, BaseAlgorithm, InputSpec, register_algorithm

gdal.UseExceptions()

_TILE = 512
_LARGE_BYTES = 200 * 1024 * 1024  # 200 MiB — mirror slope.py threshold
_NODATA = -9999.0


def _array_to_mem_ds(arr: np.ndarray, x_res: float, y_res: float) -> gdal.Dataset:
    H, W = arr.shape
    ds = gdal.GetDriverByName("MEM").Create("", W, H, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([0.0, x_res, 0.0, 0.0, 0.0, -y_res])
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    band.SetNoDataValue(_NODATA)
    return ds


def _gdal_aspect(arr: np.ndarray, x_res: float, y_res: float) -> np.ndarray:
    """Run gdaldem aspect on a 2-D float32 patch; return float32 0-360 (NaN for flat)."""
    src_ds = _array_to_mem_ds(arr, x_res, y_res)
    opts = gdal.DEMProcessingOptions(
        format="MEM",
        trigonometric=False,  # compass: 0 = north, clockwise
        zeroForFlat=False,    # flat areas → nodata rather than 0
    )
    out_ds = gdal.DEMProcessing("", src_ds, "aspect", options=opts)
    result = out_ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    nd = out_ds.GetRasterBand(1).GetNoDataValue()
    if nd is not None:
        result[result == nd] = np.nan
    del out_ds, src_ds
    return result


@register_algorithm
class Aspect(BaseAlgorithm):
    info = AlgorithmInfo(
        name="aspect",
        display="Aspect (degrees)",
        category="Terrain",
        description="Aspect from a DEM using GDAL's gdaldem aspect. Output is degrees "
                    "0-360 clockwise from north. Flat areas are NaN.",
        inputs=[InputSpec(role="dem", description="Digital elevation model", semantic="continuous")],
        params=[],
        output_name="aspect",
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        dem_arr = stack["dem"]
        n_bytes = stack.spec.height * stack.spec.width * 4
        if isinstance(dem_arr, np.memmap) or n_bytes > _LARGE_BYTES:
            return self._run_tiled(stack)
        return self._run_small(stack)

    def _run_small(self, stack: AlignedStack) -> AlignedStack:
        dem = np.asarray(stack["dem"], dtype=np.float32)
        aspect = _gdal_aspect(dem, stack.spec.x_res, stack.spec.y_res)
        return stack.with_array(self.info.output_name, aspect)

    def _run_tiled(self, stack: AlignedStack) -> AlignedStack:
        spec = stack.spec
        H, W = spec.height, spec.width
        dx, dy = spec.x_res, spec.y_res
        dem_arr = stack["dem"]

        _owned_tmpdir = None
        if stack._tmpdir is not None:
            mm_path = Path(stack._tmpdir.name) / "aspect_out.mm"
        else:
            _owned_tmpdir = tempfile.TemporaryDirectory(prefix="landplan_aspect_")
            mm_path = Path(_owned_tmpdir.name) / "aspect_out.mm"

        result = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))

        for row_off in range(0, H, _TILE):
            rh = min(_TILE, H - row_off)
            for col_off in range(0, W, _TILE):
                rw = min(_TILE, W - col_off)

                r0 = max(0, row_off - 1)
                c0 = max(0, col_off - 1)
                r1 = min(H, row_off + rh + 1)
                c1 = min(W, col_off + rw + 1)
                patch = np.array(dem_arr[r0:r1, c0:c1], dtype=np.float32)

                top   = 1 if row_off == 0       else 0
                left  = 1 if col_off == 0       else 0
                bot   = 1 if row_off + rh == H  else 0
                right = 1 if col_off + rw == W  else 0
                if top or left or bot or right:
                    patch = np.pad(patch, ((top, bot), (left, right)), mode="edge")

                aspect_patch = _gdal_aspect(patch, dx, dy)
                # Crop halo to get the tile-sized result.
                result[row_off: row_off + rh, col_off: col_off + rw] = (
                    aspect_patch[top: top + rh, left: left + rw]
                )

        result.flush()
        new_stack = stack.with_array(self.info.output_name, result)
        if _owned_tmpdir is not None:
            new_stack._tmpdir = _owned_tmpdir
        return new_stack
