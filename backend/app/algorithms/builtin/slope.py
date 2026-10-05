"""Slope (degrees) via Horn's method.

Input:  one continuous raster named "dem" (elevation in metres)
Output: slope in degrees, same grid as input

Assumes the DEM is in a projected CRS where x/y units are metres. We do NOT
detect or correct for geographic CRS in MVP - we just document the requirement.

Large-raster tiling
-------------------
When the DEM is a numpy.memmap (i.e. normalize used the large-raster path) or
the output would exceed _LARGE_BYTES, we process the raster in _TILE×_TILE
windows.  Each window reads a (tile+2)×(tile+2) patch from the memmap (which
the OS pages in on demand) so peak Python RAM ≈ 5 × _TILE² × 4 B ≈ 5 MB.
The result is written into a memmap stored in the stack's TemporaryDirectory,
so the tmpdir that kept the input memmaps alive also keeps the output alive
until write_stack_array() finishes.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from ...pipeline.stack import AlignedStack
from ..registry import AlgorithmInfo, BaseAlgorithm, InputSpec, register_algorithm

_TILE = 512
_LARGE_BYTES = 200 * 1024 * 1024  # 200 MiB — mirror normalize.py threshold


@register_algorithm
class Slope(BaseAlgorithm):
    info = AlgorithmInfo(
        name="slope",
        display="Slope (degrees)",
        category="Terrain",
        description="Slope from a DEM using Horn's 3x3 method. DEM must be in a "
                    "projected CRS (metres). Result is in degrees, 0–90.",
        inputs=[InputSpec(role="dem", description="Digital elevation model", semantic="continuous")],
        params=[],
        output_name="slope",
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        dem_arr = stack["dem"]
        n_bytes = stack.spec.height * stack.spec.width * 4
        if isinstance(dem_arr, np.memmap) or n_bytes > _LARGE_BYTES:
            return self._run_tiled(stack)
        return self._run_small(stack)

    # ------------------------------------------------------------------
    # Small-raster path (full array fits comfortably in RAM)
    # ------------------------------------------------------------------

    def _run_small(self, stack: AlignedStack) -> AlignedStack:
        dem = np.asarray(stack["dem"], dtype=np.float32)
        dx = stack.spec.x_res
        dy = stack.spec.y_res

        padded = np.pad(dem, 1, mode="edge")
        z1 = padded[:-2, :-2]; z2 = padded[:-2, 1:-1]; z3 = padded[:-2, 2:]
        z4 = padded[1:-1, :-2];                         z6 = padded[1:-1, 2:]
        z7 = padded[2:, :-2];  z8 = padded[2:, 1:-1];  z9 = padded[2:, 2:]

        dz_dx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * dx)
        dz_dy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * dy)

        slope_deg = np.degrees(np.arctan(np.sqrt(dz_dx ** 2 + dz_dy ** 2))).astype(np.float32)
        return stack.with_array(self.info.output_name, slope_deg)

    # ------------------------------------------------------------------
    # Large-raster tiled path
    # ------------------------------------------------------------------

    def _run_tiled(self, stack: AlignedStack) -> AlignedStack:
        spec = stack.spec
        H, W = spec.height, spec.width
        dx, dy = spec.x_res, spec.y_res
        dem_arr = stack["dem"]

        # Use the existing tmpdir when available so the output memmap is kept
        # alive by the same TemporaryDirectory as the input memmaps.
        _owned_tmpdir = None
        if stack._tmpdir is not None:
            mm_path = Path(stack._tmpdir.name) / "slope_out.mm"
        else:
            _owned_tmpdir = tempfile.TemporaryDirectory(prefix="landplan_slope_")
            mm_path = Path(_owned_tmpdir.name) / "slope_out.mm"

        result = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))

        for row_off in range(0, H, _TILE):
            rh = min(_TILE, H - row_off)
            for col_off in range(0, W, _TILE):
                rw = min(_TILE, W - col_off)

                # Read input patch with 1-pixel halo clamped to array bounds.
                r0 = max(0, row_off - 1)
                c0 = max(0, col_off - 1)
                r1 = min(H, row_off + rh + 1)
                c1 = min(W, col_off + rw + 1)
                # np.array() materialises the memmap slice into RAM (~1 MB).
                patch = np.array(dem_arr[r0:r1, c0:c1], dtype=np.float32)

                # Pad edges that were clamped so every tile is (rh+2)×(rw+2).
                top  = 1 if row_off == 0        else 0
                left = 1 if col_off == 0        else 0
                bot  = 1 if row_off + rh == H  else 0
                right = 1 if col_off + rw == W  else 0
                if top or left or bot or right:
                    patch = np.pad(patch, ((top, bot), (left, right)), mode="edge")

                z1 = patch[:-2, :-2]; z2 = patch[:-2, 1:-1]; z3 = patch[:-2, 2:]
                z4 = patch[1:-1, :-2];                        z6 = patch[1:-1, 2:]
                z7 = patch[2:, :-2];  z8 = patch[2:, 1:-1];  z9 = patch[2:, 2:]

                dz_dx = ((z3 + 2*z6 + z9) - (z1 + 2*z4 + z7)) / (8 * dx)
                dz_dy = ((z7 + 2*z8 + z9) - (z1 + 2*z2 + z3)) / (8 * dy)
                slope_tile = np.degrees(
                    np.arctan(np.sqrt(dz_dx**2 + dz_dy**2))
                ).astype(np.float32)
                result[row_off : row_off + rh, col_off : col_off + rw] = slope_tile

        result.flush()
        new_stack = stack.with_array(self.info.output_name, result)
        # Attach newly created tmpdir so it lives until the stack is GC'd.
        if _owned_tmpdir is not None:
            new_stack._tmpdir = _owned_tmpdir
        return new_stack
