"""Tests for backend/app/jobs/sweep.py — simplex parameter sweep."""
from __future__ import annotations

import math

import pytest

from app.algorithms.registry import AlgorithmInfo, ParamSpec
from app.jobs import sweep
from app.jobs.sweep import (
    MAX_SWEEP_SAMPLES,
    SimplexSpec,
    expand_ranges,
    parse_param_ranges,
    sweep_size,
)


# ---------- fixtures ----------

def _info_scalar() -> AlgorithmInfo:
    return AlgorithmInfo(
        name="t_scalar",
        display="T",
        category="test",
        description="",
        params=[
            ParamSpec(name="peak_deg", type="float", default=180.0),
            ParamSpec(name="exponent", type="int", default=2),
        ],
    )


def _info_list() -> AlgorithmInfo:
    return AlgorithmInfo(
        name="t_list",
        display="T",
        category="test",
        description="",
        params=[ParamSpec(name="weights", type="list[float]", default=[])],
    )


def _info_locked() -> AlgorithmInfo:
    return AlgorithmInfo(
        name="t_locked",
        display="T",
        category="test",
        description="",
        params=[ParamSpec(name="mode", type="str", default="a", sweepable=False)],
    )


# ---------- degenerate ----------

def test_degenerate_no_ranges_returns_single_base():
    info = _info_scalar()
    base = {"peak_deg": 42.0, "exponent": 3}
    combos = expand_ranges(base, {}, info)
    assert combos == [(base, "")]


# ---------- format_combo_suffix ----------

def test_format_combo_suffix_short_passthrough():
    out = sweep.format_combo_suffix(["a=1", "b=2"])
    assert out == "a=1_b=2"


def test_format_combo_suffix_truncates_long():
    bits = [f"axis{i}=value{i}" for i in range(20)]
    out = sweep.format_combo_suffix(bits)
    assert len(out) <= 80
    assert "..." in out


# ---------- simplex sampling ----------

def _info_weights(n: int) -> tuple[AlgorithmInfo, dict]:
    """AlgorithmInfo with a weights list[float] of length n."""
    info = AlgorithmInfo(
        name="t_weights",
        display="T",
        category="test",
        description="",
        params=[ParamSpec(name="weights", type="list[float]", default=[])],
    )
    base = {"weights": [1.0 / n] * n}
    return info, base


def test_simplex_parse_returns_simplex_spec():
    info, base = _info_weights(3)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 4}},
        info, base,
    )
    spec = ranges["weights"]
    assert isinstance(spec, SimplexSpec)
    assert spec.n_components == 3
    assert spec.n_divisions == 4


def test_simplex_size_matches_combinatorial_formula():
    # C(n + T - 1, T)
    for n, T in [(2, 10), (3, 4), (4, 3), (6, 3)]:
        info, base = _info_weights(n)
        ranges = parse_param_ranges(
            {"weights": {"kind": "simplex", "n_divisions": T}},
            info, base,
        )
        expected = math.comb(n + T - 1, T)
        assert sweep_size(ranges) == expected, f"n={n}, T={T}"


def test_simplex_expand_all_weights_sum_to_one():
    info, base = _info_weights(3)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 4}},
        info, base,
    )
    combos = expand_ranges(base, ranges, info)
    assert len(combos) == math.comb(3 + 4 - 1, 4)  # 15
    for params, _ in combos:
        w = params["weights"]
        assert len(w) == 3
        assert abs(sum(w) - 1.0) < 1e-9, f"weights don't sum to 1: {w}"


def test_simplex_expand_two_components_endpoints():
    # n=2, T=4 → [0,1], [0.25,0.75], [0.5,0.5], [0.75,0.25], [1,0]
    info, base = _info_weights(2)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 4}},
        info, base,
    )
    combos = expand_ranges(base, ranges, info)
    weight_vectors = [c[0]["weights"] for c in combos]
    assert weight_vectors[0] == [0.0, 1.0]
    assert weight_vectors[-1] == [1.0, 0.0]
    # All steps are 0.25
    for w in weight_vectors:
        assert all(abs(v * 4 - round(v * 4)) < 1e-9 for v in w)


def test_simplex_expand_label_format():
    info, base = _info_weights(2)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 2}},
        info, base,
    )
    combos = expand_ranges(base, ranges, info)
    labels = [c[1] for c in combos]
    assert all(lbl.startswith("weights=[") for lbl in labels)
    assert all(lbl.endswith("]") for lbl in labels)


def test_simplex_sweep_size_matches_expand_length():
    for n, T in [(2, 5), (3, 3), (4, 2)]:
        info, base = _info_weights(n)
        ranges = parse_param_ranges(
            {"weights": {"kind": "simplex", "n_divisions": T}},
            info, base,
        )
        assert sweep_size(ranges) == len(expand_ranges(base, ranges, info))


def test_simplex_size_can_exceed_cap_without_materializing():
    # C(6+3-1, 3) = C(8,3) = 56; C(6+5-1,5) = C(10,5) = 252 > 200
    info, base = _info_weights(6)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 5}},
        info, base,
    )
    n = sweep_size(ranges)
    assert n == math.comb(6 + 5 - 1, 5)
    assert n > MAX_SWEEP_SAMPLES


# ---------- validation rejections ----------

def test_reject_unknown_param():
    info = _info_list()
    with pytest.raises(ValueError, match="Unknown param"):
        parse_param_ranges(
            {"nope": {"kind": "simplex", "n_divisions": 4}},
            info, {"weights": [0.5, 0.5]},
        )


def test_reject_non_sweepable_param():
    info = _info_locked()
    with pytest.raises(ValueError, match="not sweepable"):
        parse_param_ranges(
            {"mode": {"kind": "simplex", "n_divisions": 4}},
            info, {"mode": "a"},
        )


def test_reject_unknown_kind():
    info = _info_scalar()
    with pytest.raises(ValueError, match="must be 'simplex'"):
        parse_param_ranges(
            {"peak_deg": {"kind": "range", "min": 0, "max": 1, "step": 1}},
            info, {"peak_deg": 0.0, "exponent": 2},
        )


def test_reject_missing_kind():
    info = _info_scalar()
    with pytest.raises(ValueError, match="must be an object with a 'kind' field"):
        parse_param_ranges(
            {"peak_deg": {"min": 0, "max": 1, "step": 1}},
            info, {"peak_deg": 0.0, "exponent": 2},
        )


def test_simplex_reject_on_scalar_param():
    info = _info_scalar()
    with pytest.raises(ValueError, match="only list\\[float\\] accepts kind='simplex'"):
        parse_param_ranges(
            {"peak_deg": {"kind": "simplex", "n_divisions": 4}},
            info, {"peak_deg": 0.0, "exponent": 2},
        )


def test_simplex_reject_n_divisions_zero():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="n_divisions must be >= 1"):
        parse_param_ranges(
            {"weights": {"kind": "simplex", "n_divisions": 0}},
            info, base,
        )


def test_simplex_reject_n_divisions_missing():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="n_divisions must be a positive integer"):
        parse_param_ranges(
            {"weights": {"kind": "simplex"}},
            info, base,
        )


def test_simplex_reject_n_divisions_float():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="n_divisions must be a positive integer"):
        parse_param_ranges(
            {"weights": {"kind": "simplex", "n_divisions": 4.5}},
            info, base,
        )


def test_simplex_reject_single_component():
    info = AlgorithmInfo(
        name="t_single",
        display="T",
        category="test",
        description="",
        params=[ParamSpec(name="weights", type="list[float]", default=[])],
    )
    base = {"weights": [1.0]}  # length 1
    with pytest.raises(ValueError, match="length >= 2"):
        parse_param_ranges(
            {"weights": {"kind": "simplex", "n_divisions": 4}},
            info, base,
        )


# ---------- simplex bounds ----------

def test_bounds_absent_unchanged_behavior():
    """Specs without min/max keep the unfiltered binomial count."""
    info, base = _info_weights(3)
    ranges = parse_param_ranges(
        {"weights": {"kind": "simplex", "n_divisions": 4}},
        info, base,
    )
    spec = ranges["weights"]
    assert spec.min_bounds is None
    assert spec.max_bounds is None
    assert sweep_size(ranges) == math.comb(3 + 4 - 1, 4)  # 15


def test_bounds_trivial_passes_all():
    """Explicit min=[0,...] / max=[1,...] doesn't change the sample set."""
    info, base = _info_weights(3)
    ranges = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "min": [0.0, 0.0, 0.0],
            "max": [1.0, 1.0, 1.0],
        }},
        info, base,
    )
    assert sweep_size(ranges) == math.comb(3 + 4 - 1, 4)


def test_bounds_filter_reduces_count():
    """Tight per-component bounds drop infeasible lattice points."""
    info, base = _info_weights(3)
    # n=3, T=4 → 15 unfiltered points {(a/4, b/4, c/4) : a+b+c=4}.
    # Bound max=[0.5, 0.5, 1.0] eliminates (1,0,0), (0,1,0), (0,0.75,0.25) wait
    # actually let's just check the reduction is non-trivial and every survivor
    # respects the bounds.
    ranges = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "max": [0.5, 0.5, 1.0],
        }},
        info, base,
    )
    n_filtered = sweep_size(ranges)
    assert n_filtered < math.comb(3 + 4 - 1, 4)
    assert n_filtered >= 1

    combos = expand_ranges(base, ranges, info)
    assert len(combos) == n_filtered
    for params, _ in combos:
        w = params["weights"]
        assert w[0] <= 0.5 + 1e-9
        assert w[1] <= 0.5 + 1e-9
        assert abs(sum(w) - 1.0) < 1e-9


def test_bounds_min_floor_filters():
    """A per-component minimum drops vertices touching zero."""
    info, base = _info_weights(3)
    ranges = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 4,
            "min": [0.25, 0.25, 0.25],
        }},
        info, base,
    )
    combos = expand_ranges(base, ranges, info)
    for params, _ in combos:
        for v in params["weights"]:
            assert v >= 0.25 - 1e-9


def test_bounds_eliminate_all_raises_on_expand():
    """Bounds with no surviving lattice point error out during expand_ranges."""
    info, base = _info_weights(3)
    # max=[0.1, 0.1, 0.1] makes sum(max)=0.3 < 1 → caught at parse time.
    # Instead use bounds that sum-feasibility-pass but no T=2 lattice point fits.
    ranges = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.4, 0.4, 0.0],
            "max": [0.6, 0.6, 1.0],
        }},
        info, base,
    )
    # T=2 lattice points: (1,0,0), (0,1,0), (0,0,1), (0.5,0.5,0), (0.5,0,0.5), (0,0.5,0.5)
    # min[0]>=0.4 + min[1]>=0.4 → only (0.5,0.5,0) survives → 1 sample.
    # Tighten further:
    ranges_tight = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.45, 0.45, 0.0],
            "max": [0.55, 0.55, 1.0],
        }},
        info, base,
    )
    # All vertices fail; (0.5,0.5,0) survives (0.5 within [0.45,0.55]).
    # Make it stricter so nothing fits:
    ranges_empty = parse_param_ranges(
        {"weights": {
            "kind": "simplex",
            "n_divisions": 2,
            "min": [0.45, 0.45, 0.0],
            "max": [0.49, 0.55, 1.0],
        }},
        info, base,
    )
    assert sweep_size(ranges) == 1
    assert sweep_size(ranges_tight) == 1
    assert sweep_size(ranges_empty) == 0
    with pytest.raises(ValueError, match="eliminate all"):
        expand_ranges(base, ranges_empty, info)


def test_bounds_length_mismatch_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="length 2 does not match"):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "min": [0.0, 0.0],
            }},
            info, base,
        )


def test_bounds_out_of_unit_interval_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match=r"must lie in \[0, 1\]"):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "max": [1.5, 1.0, 1.0],
            }},
            info, base,
        )
    with pytest.raises(ValueError, match=r"must lie in \[0, 1\]"):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "min": [-0.1, 0.0, 0.0],
            }},
            info, base,
        )


def test_bounds_min_gt_max_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match=r"min\[1\]=0.7 > max\[1\]=0.3"):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "min": [0.0, 0.7, 0.0],
                "max": [1.0, 0.3, 1.0],
            }},
            info, base,
        )


def test_bounds_sum_min_over_one_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="sum.min."):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "min": [0.5, 0.5, 0.5],
            }},
            info, base,
        )


def test_bounds_sum_max_under_one_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="sum.max."):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "max": [0.2, 0.2, 0.2],
            }},
            info, base,
        )


def test_bounds_non_list_rejected():
    info, base = _info_weights(3)
    with pytest.raises(ValueError, match="must be a list"):
        parse_param_ranges(
            {"weights": {
                "kind": "simplex",
                "n_divisions": 4,
                "min": "not-a-list",
            }},
            info, base,
        )
