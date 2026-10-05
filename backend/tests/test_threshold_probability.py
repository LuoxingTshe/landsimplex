"""Tests for threshold_probability algorithm."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from app.algorithms.builtin.threshold_probability import ThresholdProbability
from app.algorithms.registry import load_builtins
from app.pipeline.alignment import AlignmentSpec
from app.pipeline.stack import AlignedStack

# Ensure the algorithm registry is populated (required for _run_sub lookups)
load_builtins()

# ---------- helpers ----------


def _make_spec(H: int, W: int) -> AlignmentSpec:
    return AlignmentSpec(
        crs_wkt='PROJCS["WGS 84 / UTM zone 32N",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],PARAMETER["scale_factor",0.9996],PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]',
        x_res=10.0,
        y_res=-10.0,
        origin_x=500000.0,
        origin_y=5000000.0,
        width=W,
        height=H,
    )


def _make_stack(layer_values: list[np.ndarray]) -> AlignedStack:
    """Build a test AlignedStack from a list of 2D float32 arrays."""
    H, W = layer_values[0].shape
    spec = _make_spec(H, W)
    arrays = {f"layer_{i}": arr.astype(np.float32) for i, arr in enumerate(layer_values)}
    return AlignedStack(spec=spec, arrays=arrays)


# ---------- tests ----------


class TestThresholdProbability:
    def test_output_shape_and_dtype(self):
        """Output matches input shape and is float32 in [0, 1]."""
        H, W = 10, 10
        layers = [np.random.rand(H, W).astype(np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        result = algo.run(stack, target_score=0.5, n_divisions=2)
        prob = result["probability"]
        assert prob.shape == (H, W)
        assert prob.dtype == np.float32
        assert float(prob.min()) >= 0.0
        assert float(prob.max()) <= 1.0

    def test_target_zero_all_pass(self):
        """target_score=0 means every pixel passes every sample → prob = 1.0."""
        H, W = 5, 5
        layers = [np.full((H, W), 0.5, dtype=np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        result = algo.run(stack, target_score=0.0, n_divisions=2)
        np.testing.assert_allclose(result["probability"], 1.0, atol=1e-6)

    def test_target_one_all_fail(self):
        """target_score=1.0 means nothing passes → prob = 0.0."""
        H, W = 5, 5
        layers = [np.full((H, W), 0.5, dtype=np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        result = algo.run(stack, target_score=1.0, n_divisions=2)
        np.testing.assert_allclose(result["probability"], 0.0, atol=1e-6)

    def test_known_probability(self):
        """Deterministic inputs yield a known probability.

        n=2 layers, n_divisions=2 → C(2+2-1,2) = 3 samples:
          [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0)]

        layer_0 = all 1.0, layer_1 = all 0.0
        target_score = 0.4

        sample (0.0, 1.0): suit = 0*1 + 1*0 = 0.0 < 0.4 → 0
        sample (0.5, 0.5): suit = 0.5*1 + 0.5*0 = 0.5 ≥ 0.4 → k=1/3
        sample (1.0, 0.0): suit = 1.0*1 + 0.0*0 = 1.0 ≥ 0.4 → k=1/3
        expected = 2/3
        """
        H, W = 3, 3
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()
        result = algo.run(stack, target_score=0.4, n_divisions=2)
        expected = np.full((H, W), 2.0 / 3.0, dtype=np.float32)
        np.testing.assert_allclose(result["probability"], expected, atol=1e-6)

    def test_nan_pixels_not_accumulated(self):
        """NaN in any layer → weighted sum is NaN → never > target → prob = 0."""
        H, W = 4, 4
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.full((H, W), np.nan, dtype=np.float32)
        layer_1[0, 0] = 1.0  # one valid pixel
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()
        result = algo.run(stack, target_score=0.5, n_divisions=2)
        prob = result["probability"]
        # Valid pixel should accumulate
        assert prob[0, 0] > 0.0
        # NaN pixels get 0.0 (never pass threshold)
        assert prob[0, 1] == 0.0
        # Output should be all finite (no NaN in result)
        assert np.all(np.isfinite(prob))

    def test_n_divisions_1_two_layers(self):
        """n_divisions=1 for n=2 → C(2+1-1,1) = C(2,1) = 2 samples.

        Samples: (0.0, 1.0) and (1.0, 0.0). Each contributes k=0.5.
        layer_0=all-1.0, layer_1=all-0.0:
          (0.0,1.0): suit=0.0 → never passes threshold≥0
          (1.0,0.0): suit=1.0 → passes threshold < 1.0
        """
        H, W = 5, 5
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()

        # target=0.4: (1.0,0.0) passes → prob = k = 0.5
        result = algo.run(stack, target_score=0.4, n_divisions=1)
        np.testing.assert_allclose(result["probability"], 0.5, atol=1e-6)

        # target=0.6: (1.0,0.0) still passes → prob = 0.5
        result2 = algo.run(stack, target_score=0.6, n_divisions=1)
        np.testing.assert_allclose(result2["probability"], 0.5, atol=1e-6)

        # target=1.1 (impossible → never passes → prob=0.0)
        # Use the clamping at run: target > 1.0 is invalid, so use threshold 1.0
        # which (1.0,0.0) suit=1.0 does NOT exceed (strict >)
        result3 = algo.run(stack, target_score=1.0, n_divisions=1)
        np.testing.assert_allclose(result3["probability"], 0.0, atol=1e-6)

    def test_invalid_target_score(self):
        """target_score outside [0,1] raises ValueError."""
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(2)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        with pytest.raises(ValueError, match="target_score"):
            algo.run(stack, target_score=1.5, n_divisions=2)
        with pytest.raises(ValueError, match="target_score"):
            algo.run(stack, target_score=-0.5, n_divisions=2)

    def test_invalid_n_divisions(self):
        """n_divisions < 1 raises ValueError."""
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(2)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        with pytest.raises(ValueError, match="n_divisions"):
            algo.run(stack, target_score=0.5, n_divisions=0)

    def test_needs_at_least_two_layers(self):
        """Fewer than 2 layer_N inputs raises ValueError."""
        H, W = 5, 5
        # Only one layer — algorithm expects 2+
        stack = AlignedStack(
            spec=_make_spec(H, W),
            arrays={"layer_0": np.ones((H, W), dtype=np.float32)},
        )
        algo = ThresholdProbability()
        with pytest.raises(ValueError, match="at least 2"):
            algo.run(stack, target_score=0.5, n_divisions=2)

    def test_tiled_matches_small(self):
        """_run_tiled must produce the same result as _run_small for identical inputs.

        Creates a small (128×128) stack then forces the tiled path by converting
        layer arrays to memmap and attaching a tmpdir to the stack.
        """
        H, W = 128, 128
        rng = np.random.default_rng(42)
        layers = [rng.random((H, W), dtype=np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        layer_names = ["layer_0", "layer_1", "layer_2"]
        # Use a known sample list so we can compare exactly
        samples = [(0.4, 0.3, 0.3), (0.2, 0.5, 0.3), (0.1, 0.1, 0.8)]
        k = 1.0 / len(samples)

        # --- small path ---
        small_result = algo._run_small(
            stack, layer_names, samples, target_score=0.5, k=k
        )

        # --- tiled path ---
        # Force memmap-backed layers
        tmpdir = tempfile.TemporaryDirectory(prefix="landplan_test_")
        try:
            mm_arrays = {}
            for name in layer_names:
                mm_path = Path(tmpdir.name) / f"{name}.mm"
                mm = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))
                mm[:] = stack[name]
                mm.flush()
                mm_arrays[name] = mm
            tiled_stack = AlignedStack(
                spec=stack.spec,
                arrays=mm_arrays,
                mask=stack.mask,
                _tmpdir=tmpdir,
            )
            tiled_result = algo._run_tiled(
                tiled_stack, layer_names, samples, target_score=0.5, k=k
            )
            np.testing.assert_allclose(
                small_result["probability"],
                tiled_result["probability"],
                atol=1e-6,
                err_msg="_run_small and _run_tiled should produce identical results",
            )
        finally:
            tmpdir.cleanup()

    def test_accumulator_sums_to_one_when_all_pass(self):
        """When every pixel passes every sample, prob should sum to k*N = 1.0."""
        H, W = 8, 8
        # Create layers such that any convex combination is >= 0.5
        layers = [np.full((H, W), 0.9, dtype=np.float32) for _ in range(4)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()
        # target_score=0.5 < 0.9 → all pass
        result = algo.run(stack, target_score=0.5, n_divisions=3)
        np.testing.assert_allclose(result["probability"], 1.0, atol=1e-6)

    def test_n_divisions_controls_sample_count(self):
        """More divisions → more samples → finer probability grain.

        With layer_0=all 1.0, layer_1=all 0.0 and target=0.6:
        Only samples where w0 is large enough pass (w0 > 0.6).
        n_divisions=4 → C(6,4)=15 samples → finer grain than C(3,2)=3.
        """
        H, W = 5, 5
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()

        # T=1: 2 samples, only (1.0, 0.0) passes → prob = 0.5
        r1 = algo.run(stack, target_score=0.6, n_divisions=1)
        # T=4: 5 samples, (1.0,0.0) and (0.75,0.25) pass → prob = 2/5 = 0.4
        r4 = algo.run(stack, target_score=0.6, n_divisions=4)

        assert float(r1["probability"][0, 0]) == 0.5
        assert float(r4["probability"][0, 0]) == pytest.approx(0.4, abs=1e-6)

    # ---------- bounds tests ----------

    def test_bounds_trivial_matches_unfiltered(self):
        """min=[0,0], max=[1,1] does not filter anything — same result as no bounds."""
        H, W = 5, 5
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()

        # No bounds
        no_bounds = algo.run(stack, target_score=0.4, n_divisions=2)
        # Trivial bounds via _internal_sweep
        with_bounds = algo.run(
            stack,
            target_score=0.4,
            _internal_sweep={
                "weights": {
                    "n_divisions": 2,
                    "min": [0.0, 0.0],
                    "max": [1.0, 1.0],
                }
            },
        )
        np.testing.assert_allclose(
            no_bounds["probability"], with_bounds["probability"], atol=1e-6,
        )

    def test_bounds_filter_reduces_count(self):
        """Restrictive bounds reduce the number of surviving simplex samples.

        n=2, T=2 → full lattice: [(0,1), (0.5,0.5), (1,0)]  (3 samples)
        max=[0.5, 1.0] eliminates (1.0,0.0) → 2 survivors

        layer_0 = all 1.0, layer_1 = all 0.0, target_score = 0.4:
          no bounds:  (0,1)→0.0<0.4 pass? NO, (0.5,0.5)→0.5≥0.4 YES, (1,0)→1.0≥0.4 YES → 2/3
          with bounds: (0,1)→0.0<0.4 pass? NO,  (0.5,0.5)→0.5≥0.4 YES              → 1/2
        """
        H, W = 3, 3
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()

        result = algo.run(
            stack,
            target_score=0.4,
            _internal_sweep={
                "weights": {
                    "n_divisions": 2,
                    "max": [0.5, 1.0],
                }
            },
        )
        expected = np.full((H, W), 0.5, dtype=np.float32)  # 1/2
        np.testing.assert_allclose(result["probability"], expected, atol=1e-6)

    def test_bounds_eliminate_all_raises(self):
        """Bounds that exclude every lattice point → ValueError.

        n=2, T=1 → lattice: [(0,1), (1,0)]
        min=[0.3, 0.3] → both points fail (0 < 0.3) but sum(min)=0.6≤1 is feasible.
        """
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(2)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        with pytest.raises(ValueError, match="No simplex samples satisfy"):
            algo.run(
                stack,
                target_score=0.5,
                _internal_sweep={
                    "weights": {
                        "n_divisions": 1,
                        "min": [0.3, 0.3],
                        "max": [0.7, 0.7],
                    }
                },
            )

    def test_bounds_sum_min_over_one_raises(self):
        """sum(min) > 1 is infeasible — no weight vector can satisfy it."""
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(2)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        with pytest.raises(ValueError, match="sum\\(min\\) > 1"):
            algo.run(
                stack,
                target_score=0.5,
                _internal_sweep={
                    "weights": {
                        "n_divisions": 2,
                        "min": [0.6, 0.6],  # 1.2 > 1.0 (tolerance allows up to 1 + 1e-9)
                    }
                },
            )

    def test_bounds_sum_max_under_one_raises(self):
        """sum(max) < 1 is infeasible — no weight vector sums to < 1."""
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        with pytest.raises(ValueError, match="sum\\(max\\) < 1"):
            algo.run(
                stack,
                target_score=0.5,
                _internal_sweep={
                    "weights": {
                        "n_divisions": 2,
                        "max": [0.3, 0.3, 0.3],  # 0.9 < 1.0
                    }
                },
            )

    def test_bounds_length_mismatch_raises(self):
        """Bounds array length must match n_layers."""
        H, W = 5, 5
        layers = [np.ones((H, W), dtype=np.float32) for _ in range(2)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        with pytest.raises(ValueError, match="min length"):
            algo.run(
                stack,
                target_score=0.5,
                _internal_sweep={
                    "weights": {
                        "n_divisions": 2,
                        "min": [0.1, 0.2, 0.3],  # 3 values for 2 layers
                    }
                },
            )

    def test_tiled_matches_small_with_bounds(self):
        """Tiled path with bounds produces same result as small path."""
        H, W = 128, 128
        rng = np.random.default_rng(42)
        layers = [rng.random((H, W), dtype=np.float32) for _ in range(3)]
        stack = _make_stack(layers)
        algo = ThresholdProbability()

        layer_names = ["layer_0", "layer_1", "layer_2"]
        samples = [(0.4, 0.3, 0.3), (0.2, 0.5, 0.3)]
        k = 1.0 / len(samples)

        # --- small path ---
        small_result = algo._run_small(
            stack, layer_names, samples, target_score=0.5, k=k
        )

        # --- tiled path (force memmap) ---
        tmpdir = tempfile.TemporaryDirectory(prefix="landplan_test_")
        try:
            mm_arrays = {}
            for name in layer_names:
                mm_path = Path(tmpdir.name) / f"{name}.mm"
                mm = np.memmap(mm_path, dtype="float32", mode="w+", shape=(H, W))
                mm[:] = stack[name]
                mm.flush()
                mm_arrays[name] = mm
            tiled_stack = AlignedStack(
                spec=stack.spec,
                arrays=mm_arrays,
                mask=stack.mask,
                _tmpdir=tmpdir,
            )
            tiled_result = algo._run_tiled(
                tiled_stack, layer_names, samples, target_score=0.5, k=k
            )
            np.testing.assert_allclose(
                small_result["probability"],
                tiled_result["probability"],
                atol=1e-6,
                err_msg="tiled and small path should match with bounds",
            )
        finally:
            tmpdir.cleanup()

    def test_single_survivor_bounds(self):
        """Bounds that leave exactly one lattice point → prob is 0 or 1 per pixel.

        n=2, T=2 → [(0,1), (0.5,0.5), (1,0)]
        max=[0.4, 1.0] leaves only (0,1): w0=0, w1=1
        layer_0=1.0, layer_1=0.0: suit = 0*1 + 1*0 = 0.0 < target=0.4 → prob=0.0
        """
        H, W = 5, 5
        layer_0 = np.ones((H, W), dtype=np.float32)
        layer_1 = np.zeros((H, W), dtype=np.float32)
        stack = _make_stack([layer_0, layer_1])
        algo = ThresholdProbability()

        result = algo.run(
            stack,
            target_score=0.4,
            _internal_sweep={
                "weights": {
                    "n_divisions": 2,
                    "max": [0.4, 1.0],
                }
            },
        )
        # Only (0,1) survives → suit=0.0 → never > 0.4 → prob=0
        np.testing.assert_allclose(result["probability"], 0.0, atol=1e-6)
