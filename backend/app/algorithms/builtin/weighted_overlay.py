"""Weighted overlay (suitability analysis).

Given N continuous rasters (already normalized to a comparable scale, e.g. 0-1)
and N weights, produce a weighted sum. The weights are normalised internally
so they sum to 1.

Input roles are dynamic: layer_0, layer_1, ... layer_N-1. The runner is
responsible for assembling them in the right order before calling run().

This algorithm primarily exists to validate that normalize() correctly aligns
multiple rasters with different CRS / resolution / extent.

Large-raster tiling
-------------------
When any input is a numpy.memmap or the output would exceed _LARGE_BYTES, we
process in _TILE×_TILE windows.  Peak RAM per tile ≈ (N+1) × _TILE² × 4 B.
For N=6 that is ≈ 7 MB, well within the 2 GiB budget.
The result memmap is stored in the stack's TemporaryDirectory so it remains
valid until write_stack_array() has flushed it to a COG.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from ...pipeline.stack import AlignedStack
from ..registry import (
    AlgorithmInfo,
    BaseAlgorithm,
    DynamicInputSpec,
    ParamSpec,
    register_algorithm,
)

_TILE = 512
_LARGE_BYTES = 200 * 1024 * 1024  # 200 MiB


@register_algorithm
class WeightedOverlay(BaseAlgorithm):
    info = AlgorithmInfo(
        name="weighted_overlay",
        display="Weighted overlay",
        category="Suitability",
        description="Weighted sum of N input rasters. Inputs should be pre-normalised "
                    "to a comparable scale (e.g. 0-1). Weights are renormalised to sum to 1.",
        inputs=[],
        params=[
            ParamSpec(
                name="weights",
                type="list[float]",
                default=[],
                description="One weight per input raster, in order.",
            ),
        ],
        output_name="suitability",
        dynamic_inputs=DynamicInputSpec(
            role_prefix="layer_",
            description="Suitability factor (continuous raster, ideally 0–1 normalised)",
            semantic="continuous",
            min_count=2,
            max_count=6,
            paired_param="weights",
        ),
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        weights = params.get("weights") or []
        layer_names = sorted(
            [k for k in stack.arrays if k.startswith("layer_")],
            key=lambda s: int(s.split("_")[1]),
        )
        if not layer_names:
            raise ValueError("weighted_overlay needs at least one layer_N input")
        if len(weights) != len(layer_names):
            raise ValueError(f"Got {len(weights)} weights but {len(layer_names)} layers")

        w = np.asarray(weights, dtype=np.float32)
        if w.sum() == 0:
            raise ValueError("Weights sum to zero")
        w = w / w.sum()

        n_bytes = stack.spec.height * stack.spec.width * 4
        any_memmap = any(isinstance(stack[n], np.memmap) for n in layer_names)
        if any_memmap or n_bytes > _LARGE_BYTES:
            return self._run_tiled(stack, layer_names, w)
        return self._run_small(stack, layer_names, w)

    # ------------------------------------------------------------------
    # Small-raster path
    # ------------------------------------------------------------------

    def _run_small(
        self, stack: AlignedStack, layer_names: list[str], w: np.ndarray
    ) -> AlignedStack:
        # Stack layers into (N, H, W) and compute weighted sum.
        # For small rasters this single allocation is acceptable.
        layers = np.stack(
            [np.asarray(stack[n], dtype=np.float32) for n in layer_names], axis=0
        )
        result = np.tensordot(w, layers, axes=([0], [0])).astype(np.float32)
        return stack.with_array(self.info.output_name, result)

    # ------------------------------------------------------------------
    # Large-raster tiled path
    # ------------------------------------------------------------------

    def _run_tiled(
        self, stack: AlignedStack, layer_names: list[str], w: np.ndarray
    ) -> AlignedStack:
        spec = stack.spec
        H, W = spec.height, spec.width

        _owned_tmpdir = None
        if stack._tmpdir is not None:
            mm_path = Path(stack._tmpdir.name) / "suitability_out.mm"
        else:
            _owned_tmpdir = tempfile.TemporaryDirectory(prefix="landplan_wo_")
            mm_path = Path(_owned_tmpdir.name) / "suitability_out.mm"

        result = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))

        for row_off in range(0, H, _TILE):
            rh = min(_TILE, H - row_off)
            for col_off in range(0, W, _TILE):
                rw = min(_TILE, W - col_off)

                tile_sum = np.zeros((rh, rw), dtype=np.float32)
                for wi, name in zip(w, layer_names):
                    # Materialise one tile from each (possibly memmap) layer.
                    layer_tile = np.array(
                        stack[name][row_off : row_off + rh, col_off : col_off + rw],
                        dtype=np.float32,
                    )
                    tile_sum += wi * layer_tile

                result[row_off : row_off + rh, col_off : col_off + rw] = tile_sum

        result.flush()
        new_stack = stack.with_array(self.info.output_name, result)
        if _owned_tmpdir is not None:
            new_stack._tmpdir = _owned_tmpdir
        return new_stack
