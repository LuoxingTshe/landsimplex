"""Jobs API: list algorithms, submit, poll status, parameter sweeps."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..algorithms import registry
from ..jobs import runner
from ..jobs import sweep as sweep_mod
from ..storage import local as store

router = APIRouter(tags=["jobs"])


@router.get("/algorithms")
def list_algorithms() -> list[dict]:
    return [asdict(info) for info in registry.list_all()]


class SubmitRequest(BaseModel):
    algorithm: str
    inputs: dict[str, str]   # role -> raster_id
    params: dict = {}
    # Optional parameter-sweep payload (see backend/app/jobs/sweep.py).
    # When None or expanded to a single combo, behavior is identical to a
    # single-value submission and the response shape is unchanged.
    param_ranges: dict | None = None


class PreviewRequest(BaseModel):
    """Lightweight payload for /jobs/preview — no inputs required."""
    algorithm: str
    params: dict = {}
    param_ranges: dict | None = None


@router.post("/jobs/preview")
def preview_job(req: PreviewRequest) -> dict:
    """Resolve param_ranges and return the resulting sample count.

    Does not enqueue any job. Used by the frontend to show a live "将运行 N 次"
    counter when bounds make local computation infeasible. Returns:
      { sample_count: int, max_samples: int }
    400 on validation failure (over-cap, bounds eliminate everything, ...).
    """
    try:
        algo_cls = registry.get(req.algorithm)
    except KeyError:
        raise HTTPException(404, f"Unknown algorithm: {req.algorithm}")

    if not req.param_ranges:
        return {"sample_count": 1, "max_samples": sweep_mod.MAX_SWEEP_SAMPLES}

    try:
        ranges = sweep_mod.parse_param_ranges(
            req.param_ranges, algo_cls.info, req.params
        )
        n = sweep_mod.sweep_size(ranges)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if n == 0:
        # parse_param_ranges accepted the bounds (sum-feasible), but no lattice
        # point sits inside them at the chosen n_divisions. Surface this early.
        raise HTTPException(
            400,
            "bounds eliminate every lattice point at the chosen n_divisions; "
            "raise n_divisions or widen the bounds",
        )
    return {"sample_count": n, "max_samples": sweep_mod.MAX_SWEEP_SAMPLES}


@router.post("/jobs")
def submit_job(req: SubmitRequest) -> dict:
    try:
        algo_cls = registry.get(req.algorithm)
    except KeyError:
        raise HTTPException(404, f"Unknown algorithm: {req.algorithm}")

    for role, raster_id in req.inputs.items():
        if store.get_raster(raster_id) is None:
            raise HTTPException(400, f"Input raster '{raster_id}' not found")

    # ---- composite internal-sweep path -----------------------------------
    # Composite algorithms like threshold_probability run their own simplex
    # sweep in-process so they can accumulate results into a shared array.
    # We must redirect param_ranges into a regular param rather than creating
    # sweep children (which would run in separate processes).
    if req.param_ranges and algo_cls.info.is_composite:
        req.params["_internal_sweep"] = req.param_ranges
        req.param_ranges = None  # fall through to single-job path below

    # ---- single-job path ------------------------------------------------
    if not req.param_ranges:
        job_id = store.insert_job(req.algorithm, req.params, req.inputs)
        runner.submit(job_id, req.algorithm, req.params, req.inputs)
        return store.get_job(job_id)

    # ---- sweep path -----------------------------------------------------
    try:
        ranges = sweep_mod.parse_param_ranges(
            req.param_ranges, algo_cls.info, req.params
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    n = sweep_mod.sweep_size(ranges)
    if n == 0:
        raise HTTPException(
            400,
            "bounds eliminate every lattice point at the chosen n_divisions; "
            "raise n_divisions or widen the bounds",
        )
    if n > sweep_mod.MAX_SWEEP_SAMPLES:
        raise HTTPException(
            400,
            f"Sweep produces {n} samples, exceeds cap of {sweep_mod.MAX_SWEEP_SAMPLES}",
        )

    try:
        combos = sweep_mod.expand_ranges(req.params, ranges, algo_cls.info)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if n == 1:
        # Single-combo result: e.g. bounds left exactly one feasible weight
        # vector. Run it as a normal job, but using the resolved params from
        # expand_ranges so the user gets the survivor — not the base values.
        resolved_params, _ = combos[0]
        job_id = store.insert_job(req.algorithm, resolved_params, req.inputs)
        runner.submit(job_id, req.algorithm, resolved_params, req.inputs)
        return store.get_job(job_id)
    parent_id = store.insert_sweep_job(
        req.algorithm, req.params, req.param_ranges, req.inputs, sample_count=n
    )
    for i, (resolved_params, label) in enumerate(combos):
        child_id = store.insert_job(
            req.algorithm,
            resolved_params,
            req.inputs,
            kind="sweep_child",
            parent_id=parent_id,
            sample_label=label,
            sample_index=i,
        )
        runner.submit(child_id, req.algorithm, resolved_params, req.inputs)
    return store.get_job(parent_id)


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@router.get("/jobs/{job_id}/children")
def get_job_children(job_id: str) -> list[dict]:
    parent = store.get_job(job_id)
    if parent is None:
        raise HTTPException(404, f"Job {job_id} not found")
    if parent.get("kind") != "sweep":
        raise HTTPException(400, f"Job {job_id} is not a sweep")
    return store.list_children(job_id)
