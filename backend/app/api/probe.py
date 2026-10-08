"""Pixel probe: per-pixel layer values + the weight-space lattice behind a result.

A pixel's WLC score is linear in the weights, score(w) = Σ wᵢ·vᵢ, so the
frontend only needs the pixel's n layer values and the lattice to recompute
pass/fail for every weight vector (and re-threshold instantly). For a
threshold_probability result, mean(lattice·values > threshold) equals the
pixel's map value.

Supported probe targets:
  - threshold_probability output  → lattice from params['_internal_sweep'],
                                    threshold = params['target_score']
  - weighted_overlay sweep child  → lattice from the parent's param_ranges,
                                    threshold = None (frontend slider)
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from fastapi import APIRouter, HTTPException, Query
from rasterio.transform import Affine
from rasterio.warp import reproject, transform as warp_transform

from ..algorithms import registry
from ..jobs import sweep as sweep_mod
from ..jobs.runner import _build_resampling_map
from ..storage import local as store

router = APIRouter(prefix="/probe", tags=["probe"])


def _not_probeable(message: str) -> HTTPException:
    return HTTPException(400, {"code": "not_probeable", "message": message})


def _layer_roles(inputs: dict[str, str]) -> list[str]:
    return sorted(
        (r for r in inputs if r.startswith("layer_")), key=lambda s: int(s.split("_")[1])
    )


def _resolve_lattice(job: dict) -> tuple[str, dict, dict, float | None]:
    """Return (mode, inputs, simplex_spec_dict, threshold) for a probe-able job."""
    if job["algorithm"] == "threshold_probability":
        params = job["params"]
        spec = dict(params.get("_internal_sweep", {}).get("weights", {}))
        # Mirror ThresholdProbability.run(): spec first, then param, then 4.
        if spec.get("n_divisions") is None:
            spec["n_divisions"] = int(params.get("n_divisions", 4))
        return "threshold_probability", job["inputs"], spec, float(params.get("target_score", 0.7))

    if job["algorithm"] == "weighted_overlay" and job.get("kind") == "sweep_child":
        parent = store.get_job(job["parent_id"])
        ranges = (parent or {}).get("param_ranges") or {}
        if set(ranges) != {"weights"}:
            raise _not_probeable("probe supports sweeps over 'weights' only")
        return "overlay_sweep", parent["inputs"], dict(ranges["weights"]), None

    raise _not_probeable(
        f"'{job['algorithm']}' results have no weight space to probe; "
        "use a threshold_probability result or a weighted_overlay sweep child"
    )


def _sample_pixel(path: Path, dst_crs, dst_transform: Affine, resampling) -> float:
    """Warp `path` onto one destination pixel, exactly as normalize() would."""
    dst = np.full((1, 1), np.nan, dtype=np.float32)
    with rasterio.open(path) as src:
        reproject(
            source=rasterio.band(src, 1),
            destination=dst,
            src_nodata=src.nodata,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            dst_nodata=np.nan,
            resampling=resampling,
        )
    return float(dst[0, 0])


@router.get("/{raster_id}")
def probe(
    raster_id: str,
    lon: float = Query(..., ge=-180, le=180),
    lat: float = Query(..., ge=-90, le=90),
) -> dict:
    target = store.get_raster(raster_id)
    if target is None:
        raise HTTPException(404, f"Raster {raster_id} not found")
    job = store.find_job_by_output(raster_id)
    if job is None:
        raise _not_probeable("not an analysis result")

    mode, inputs, spec_raw, threshold = _resolve_lattice(job)
    roles = _layer_roles(inputs)
    n = len(roles)
    try:
        spec = sweep_mod._parse_simplex(spec_raw, [0.0] * n, "weights")
        lattice = sweep_mod._simplex_samples(spec)
    except ValueError as e:
        raise _not_probeable(str(e))

    with rasterio.open(target["cog_path"]) as out:
        xs, ys = warp_transform("EPSG:4326", out.crs, [lon], [lat])
        row, col = out.index(xs[0], ys[0])
        if not (0 <= row < out.height and 0 <= col < out.width):
            raise HTTPException(404, "point is outside the raster")
        out_crs = out.crs
        px_transform = out.transform @ Affine.translation(col, row)
        output_value = float(out.read(1, window=((row, row + 1), (col, col + 1)))[0, 0])
        if out.nodata is not None and output_value == out.nodata:
            output_value = math.nan

    corners = [px_transform @ c for c in ((0, 0), (1, 0), (1, 1), (0, 1))]
    flon, flat = warp_transform(out_crs, "EPSG:4326", [c[0] for c in corners], [c[1] for c in corners])

    resampling = _build_resampling_map(registry.get(job["algorithm"]).info, roles)
    layers, values = [], []
    for role in roles:
        meta = store.get_raster(inputs[role])
        if meta is None:
            raise _not_probeable(f"input raster for {role} was deleted")
        layers.append({"role": role, "raster_id": meta["id"], "name": meta["name"]})
        values.append(_sample_pixel(Path(meta["cog_path"]), out_crs, px_transform, resampling[role]))

    def _num(v: float) -> float | None:
        return None if math.isnan(v) else v

    return {
        "job_id": job["id"],
        "mode": mode,
        "layers": layers,
        "pixel": {"row": row, "col": col, "footprint": [list(p) for p in zip(flon, flat)]},
        "valid": not any(math.isnan(v) for v in values),
        "values": [_num(v) for v in values],
        "lattice": [list(w) for w in lattice],
        "n_divisions": spec.n_divisions,
        "min": list(spec.min_bounds) if spec.min_bounds else None,
        "max": list(spec.max_bounds) if spec.max_bounds else None,
        "threshold": threshold,
        "output_value": _num(output_value),
    }
