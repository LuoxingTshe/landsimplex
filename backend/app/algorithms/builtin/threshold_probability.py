"""Threshold-probability density via simplex-sampled WLC.

For each simplex weight vector, runs weighted_overlay internally, threshold-
tests the result against a user-supplied target_score, and accumulates 1/N at
qualifying pixels.  The output is the fraction of sweeps that passed the
threshold at each pixel — a [0,1] probability map.

The simplex grid can be constrained by per-weight-factor min/max bounds,
set via the frontend simplex UI (paired_param="weights").  The bounds are
forwarded by the API as _internal_sweep and applied in-process, since the
children of a job-level sweep would run in separate processes and cannot
share an accumulator.

Large-raster tiling
-------------------
When any input is a numpy.memmap or the output would exceed _LARGE_BYTES, we
process in _TILE×_TILE windows and inline the WLC calculation to avoid
creating a full-size intermediate suitability array per simplex sample.
Peak RAM per tile ≈ (N+2) × _TILE² × 4 B; for N=6 that is ≈ 8 MB.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

from ...pipeline.stack import AlignedStack
from ..composite import CompositeAlgorithm
from ..registry import (
    AlgorithmInfo,
    DynamicInputSpec,
    ParamSpec,
    register_algorithm,
)

_TILE = 512
_LARGE_BYTES = 200 * 1024 * 1024  # 200 MiB

# nimplex native extension lives in backend/vendor/nimplex.so
_VENDOR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "vendor"


def _nimplex():
    """Lazy-import nimplex from vendor/, raising ImportError with a clear message."""
    vendor = str(_VENDOR_DIR)
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    try:
        import nimplex as _nx  # noqa: PLC0415
        return _nx
    except ImportError as e:
        raise ImportError(
            f"nimplex.so not found in {_VENDOR_DIR}. "
            "Build it from https://github.com/amkrajewski/nimplex "
            "and copy nimplex.so into backend/vendor/."
        ) from e


@register_algorithm
class ThresholdProbability(CompositeAlgorithm):
    info = AlgorithmInfo(
        name="threshold_probability",
        display="WLC阈值概率密度",
        category="Suitability",
        description=(
            "通过单纯形采样统计WLC结果超过目标阈值的概率分布。"
            "对每个单纯形权重组合运行加权叠加，统计每个像素在所有采样中"
            "超过target_score的比例，输出[0,1]概率密度图。"
        ),
        inputs=[],
        params=[
            ParamSpec(
                name="target_score",
                type="float",
                default=0.7,
                description="目标评分阈值[0,1]；像素值超过此值即计入通过",
                sweepable=False,
                min=0.0,
                max=1.0,
            ),
            ParamSpec(
                name="weights",
                type="list[float]",
                default=[],
                description="每层权重（单纯形采样激活时忽略固定值，由采样生成）",
                sweepable=True,
            ),
        ],
        output_name="probability",
        dynamic_inputs=DynamicInputSpec(
            role_prefix="layer_",
            description="适宜性因子（连续栅格，建议归一化到0-1）",
            semantic="continuous",
            min_count=2,
            max_count=6,
            paired_param="weights",  # 前端渲染 simplex UI + 每层权重/区间输入
        ),
        is_composite=True,
    )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        target_score = float(params.get("target_score", 0.7))

        # ---- internal sweep config (injected by jobs.py for composite algos) ----
        internal_sweep = params.pop("_internal_sweep", {})
        weights_spec = internal_sweep.get("weights", {})

        # n_divisions: simplex spec first, then kwarg fallback, then default 4
        # Use explicit None checks — `or` would treat 0 as falsy and bypass validation.
        spec_t = weights_spec.get("n_divisions")
        if spec_t is not None:
            n_divisions = int(spec_t)
        elif "n_divisions" in params:
            n_divisions = int(params["n_divisions"])
        else:
            n_divisions = 4

        if not (0.0 <= target_score <= 1.0):
            raise ValueError("target_score must be in [0, 1]")
        if n_divisions < 1:
            raise ValueError("n_divisions must be >= 1")

        # Enumerate dynamic inputs in sorted order (same pattern as weighted_overlay)
        layer_names = sorted(
            [k for k in stack.arrays if k.startswith("layer_")],
            key=lambda s: int(s.split("_")[1]),
        )
        if len(layer_names) < 2:
            raise ValueError(
                "threshold_probability needs at least 2 layer_N inputs"
            )

        # ---- optional per-component bounds ----
        nx = _nimplex()
        n = len(layer_names)
        raw_mins = weights_spec.get("min")
        raw_maxs = weights_spec.get("max")
        weight_mins: tuple[float, ...] | None = None
        weight_maxs: tuple[float, ...] | None = None
        if raw_mins is not None:
            if len(raw_mins) != n:
                raise ValueError(
                    f"weights min length {len(raw_mins)} != n_layers {n}"
                )
            weight_mins = tuple(float(v) for v in raw_mins)
        if raw_maxs is not None:
            if len(raw_maxs) != n:
                raise ValueError(
                    f"weights max length {len(raw_maxs)} != n_layers {n}"
                )
            weight_maxs = tuple(float(v) for v in raw_maxs)

        # Feasibility check (mirrors sweep.py _parse_simplex)
        lo = weight_mins if weight_mins is not None else (0.0,) * n
        hi = weight_maxs if weight_maxs is not None else (1.0,) * n
        _BOUND_TOL = 1e-9
        if sum(lo) > 1.0 + _BOUND_TOL:
            raise ValueError("sum(min) > 1; no weight vector is feasible")
        if sum(hi) < 1.0 - _BOUND_TOL:
            raise ValueError("sum(max) < 1; no weight vector is feasible")

        # Generate and filter simplex lattice
        raw = nx.simplex_grid_fractional_py(n, n_divisions)
        samples = [
            tuple(row) for row in raw
            if all(lo[i] - _BOUND_TOL <= row[i] <= hi[i] + _BOUND_TOL for i in range(n))
        ]
        if not samples:
            raise ValueError(
                "No simplex samples satisfy the given bounds at the chosen "
                "n_divisions; raise n_divisions or widen the bounds"
            )
        k = 1.0 / len(samples)  # per-sample accumulation fraction

        # Detect large-raster condition
        n_bytes = stack.spec.height * stack.spec.width * 4
        any_memmap = any(isinstance(stack[nm], np.memmap) for nm in layer_names)
        if any_memmap or n_bytes > _LARGE_BYTES:
            return self._run_tiled(stack, layer_names, samples, target_score, k)
        return self._run_small(stack, layer_names, samples, target_score, k)

    # ------------------------------------------------------------------
    # Small-raster path: delegate to weighted_overlay via _run_sub
    # ------------------------------------------------------------------

    def _run_small(
        self,
        stack: AlignedStack,
        layer_names: list[str],
        samples: list[tuple[float, ...]],
        target_score: float,
        k: float,
    ) -> AlignedStack:
        H, W = stack.spec.height, stack.spec.width
        acc = np.zeros((H, W), dtype=np.float32)

        for weight_vec in samples:
            overlay = self._run_sub(
                "weighted_overlay",
                stack,
                weights=list(weight_vec),
            )
            suitability = np.asarray(overlay["suitability"], dtype=np.float32)
            # NaN: NaN > target_score is False, so masked pixels get 0.0
            acc += np.where(suitability > target_score, k, np.float32(0.0))

        return stack.with_array(self.info.output_name, acc)

    # ------------------------------------------------------------------
    # Large-raster tiled path: inline WLC to avoid per-sample memmaps
    # ------------------------------------------------------------------

    def _run_tiled(
        self,
        stack: AlignedStack,
        layer_names: list[str],
        samples: list[tuple[float, ...]],
        target_score: float,
        k: float,
    ) -> AlignedStack:
        spec = stack.spec
        H, W = spec.height, spec.width

        # Resolve TemporaryDirectory ownership (same pattern as weighted_overlay)
        _owned_tmpdir = None
        if stack._tmpdir is not None:
            mm_path = Path(stack._tmpdir.name) / "probability_out.mm"
        else:
            _owned_tmpdir = tempfile.TemporaryDirectory(prefix="landplan_tp_")
            mm_path = Path(_owned_tmpdir.name) / "probability_out.mm"

        result = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))

        # Three-level nested loop:
        #   outer  — tile position (512×512)
        #   middle — read one tile from every layer (N × 1 MB)
        #   inner  — for each simplex sample, compute weighted sum, threshold,
        #            accumulate
        for row_off in range(0, H, _TILE):
            rh = min(_TILE, H - row_off)
            for col_off in range(0, W, _TILE):
                rw = min(_TILE, W - col_off)

                # Step 1: read this tile from every layer once
                layer_tiles = [
                    np.array(
                        stack[name][row_off : row_off + rh, col_off : col_off + rw],
                        dtype=np.float32,
                    )
                    for name in layer_names
                ]

                # Step 2: accumulator tile (float32)
                tile_acc = np.zeros((rh, rw), dtype=np.float32)

                # Step 3: inner WLC loop over simplex samples
                for weight_vec in samples:
                    # Inline weighted sum: tile_suit = Σ wi * layer_tiles[i]
                    tile_suit = weight_vec[0] * layer_tiles[0]
                    for wi, lt in zip(weight_vec[1:], layer_tiles[1:]):
                        tile_suit += wi * lt
                    # Threshold and accumulate (bool → float32 multiply)
                    tile_acc += (tile_suit > target_score).astype(np.float32) * k

                result[row_off : row_off + rh, col_off : col_off + rw] = tile_acc

        result.flush()
        new_stack = stack.with_array(self.info.output_name, result)
        if _owned_tmpdir is not None:
            new_stack._tmpdir = _owned_tmpdir
        return new_stack
