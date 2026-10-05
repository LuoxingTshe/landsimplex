"""Base class for composite algorithms.

A composite algorithm orchestrates other registered algorithms (slope, aspect,
weighted_overlay, ...) and applies reclassification rules between them to
produce higher-level landscape analyses.

Subclasses still implement run(); this class only provides reusable helpers:
  _run_sub:             call another registered algorithm by name
  _reclassify_linear:   remap stack[role] to [0,1] (linear, with clip + invert)
  _reclassify_cosine:   remap stack[role] (in degrees) to [0,1] peaking at peak_deg

The reclassify helpers honour the codebase's large-raster contract: if the
source array is a numpy.memmap (i.e. > 200 MiB after normalize), the output is
written into the stack's existing TemporaryDirectory as another memmap and
processed 512×512 at a time. Peak Python RAM per tile ≈ 2 × 1 MB.
NaN is preserved through both reclassifiers.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from ..pipeline.stack import AlignedStack
from .registry import BaseAlgorithm, get as registry_get

_TILE = 512
_LARGE_BYTES = 200 * 1024 * 1024


class CompositeAlgorithm(BaseAlgorithm):
    def _run_sub(self, name: str, stack: AlignedStack, **params) -> AlignedStack:
        return registry_get(name)().run(stack, **params)

    # ------------------------------------------------------------------
    # Reclassification helpers
    # ------------------------------------------------------------------

    @classmethod
    def _reclassify_linear(
        cls,
        stack: AlignedStack,
        role: str,
        in_lo: float,
        in_hi: float,
        invert: bool = False,
    ) -> np.ndarray:
        if in_hi == in_lo:
            raise ValueError("_reclassify_linear: in_hi must differ from in_lo")

        def kernel(a: np.ndarray) -> np.ndarray:
            score = (a - in_lo) / (in_hi - in_lo)
            score = np.clip(score, 0.0, 1.0)
            if invert:
                score = 1.0 - score
            return np.where(np.isnan(a), np.float32(np.nan), score).astype(np.float32)

        return cls._apply_pixelwise(stack, role, kernel, mm_tag="reclin")

    @classmethod
    def _reclassify_cosine(
        cls,
        stack: AlignedStack,
        role: str,
        peak_deg: float = 225.0,
    ) -> np.ndarray:
        def kernel(a: np.ndarray) -> np.ndarray:
            score = (1.0 + np.cos(np.radians(a - peak_deg))) / 2.0
            return np.where(np.isnan(a), np.float32(np.nan), score).astype(np.float32)

        return cls._apply_pixelwise(stack, role, kernel, mm_tag="reccos")

    # ------------------------------------------------------------------
    # Internal: per-pixel kernel runner that picks small vs tiled path
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_pixelwise(
        stack: AlignedStack,
        role: str,
        kernel,
        mm_tag: str,
    ) -> np.ndarray:
        arr = stack[role]
        H, W = stack.spec.height, stack.spec.width
        n_bytes = H * W * 4
        if not isinstance(arr, np.memmap) and n_bytes <= _LARGE_BYTES:
            return kernel(np.asarray(arr, dtype=np.float32))

        if stack._tmpdir is None:
            raise RuntimeError(
                "Large-raster reclassify requires stack._tmpdir; sub-algorithm "
                "did not provision one. Make sure aspect/slope ran first."
            )
        mm_path = Path(stack._tmpdir.name) / f"{mm_tag}_{uuid.uuid4().hex[:8]}.mm"
        out = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))
        for r in range(0, H, _TILE):
            rh = min(_TILE, H - r)
            for c in range(0, W, _TILE):
                rw = min(_TILE, W - c)
                tile = np.array(arr[r : r + rh, c : c + rw], dtype=np.float32)
                out[r : r + rh, c : c + rw] = kernel(tile)
        out.flush()
        return out
