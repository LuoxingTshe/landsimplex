"""Job runner.

For MVP we use a small ProcessPoolExecutor. Each algorithm invocation runs in
a worker process, freeing the FastAPI event loop from numpy/GDAL work and
escaping the GIL.

Why processes, not threads:
  - rasterio / numpy / GDAL release the GIL on heavy ops but not always
  - process isolation makes a crashed algorithm not crash the API server

We deliberately do not use Celery/RQ/arq - those need a broker (Redis) which
would violate the "single-machine, zero external deps" MVP goal.
"""
from __future__ import annotations

import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

from rasterio.warp import Resampling

from ..algorithms import registry
from ..config import RESULT_DIR
from ..pipeline.normalize import normalize
from ..pipeline.writer import write_stack_array
from ..storage import local as store

# Module-level executor. Lazy-init so import is cheap.
_executor: ProcessPoolExecutor | None = None


def get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None:
        # max_workers=2 is plenty for MVP. More workers means more concurrent
        # GDAL processes loading large rasters into RAM.
        _executor = ProcessPoolExecutor(max_workers=2, initializer=_worker_init)
    return _executor


def shutdown() -> None:
    """Cancel queued jobs and kill workers so process exit isn't blocked by a running job."""
    global _executor
    if _executor is None:
        return
    # Python 3.11 has no terminate_workers(); grab the private process map first
    # because shutdown() clears it.
    procs = list((_executor._processes or {}).values())
    _executor.shutdown(wait=False, cancel_futures=True)
    for p in procs:
        p.terminate()
    _executor = None


def _worker_init() -> None:
    """Run in each worker process. Load algorithms so registry is populated."""
    registry.load_builtins()


def submit(job_id: str, algorithm: str, params: dict, inputs: dict[str, str]) -> None:
    """Submit a job to the pool. Returns immediately; job updates DB itself."""
    get_executor().submit(_run_job, job_id, algorithm, params, inputs)


def _resampling_for_semantic(semantic: str) -> Resampling:
    """Categorical rasters must use nearest-neighbour or class labels get destroyed."""
    return Resampling.nearest if semantic == "categorical" else Resampling.bilinear


def _build_resampling_map(
    info: registry.AlgorithmInfo, roles: list[str]
) -> dict[str, Resampling]:
    """Derive per-role Resampling from the algorithm's declared semantics.

    Roles matched by `info.inputs[].role` use that InputSpec's semantic.
    Roles matched by `info.dynamic_inputs.role_prefix` use the dynamic spec.
    Anything else falls back to bilinear (continuous).
    """
    declared = {inp.role: inp.semantic for inp in info.inputs}
    dyn = info.dynamic_inputs
    out: dict[str, Resampling] = {}
    for role in roles:
        if role in declared:
            sem = declared[role]
        elif dyn is not None and role.startswith(dyn.role_prefix):
            sem = dyn.semantic
        else:
            sem = "continuous"
        out[role] = _resampling_for_semantic(sem)
    return out


def _run_job(job_id: str, algorithm_name: str, params: dict, inputs: dict[str, str]) -> None:
    """Worker entry point. Runs the algorithm and writes the result COG.

    All exceptions are caught and written to the job record - they must not
    propagate to the executor or it'd silently swallow them.
    """
    try:
        store.update_job(job_id, status="running", progress=0.05, message="loading inputs")

        algo_cls = registry.get(algorithm_name)
        algo = algo_cls()

        # Map role -> file path
        source_paths: dict[str, Path] = {}
        for role, raster_id in inputs.items():
            meta = store.get_raster(raster_id)
            if meta is None:
                raise ValueError(f"Input raster '{raster_id}' not found")
            source_paths[role] = Path(meta["cog_path"])

        resampling_map = _build_resampling_map(algo.info, list(source_paths))

        store.update_job(job_id, progress=0.2, message="normalizing")
        stack = normalize(
            source_paths,
            bounds_policy="union",
            resampling=resampling_map,
        )

        store.update_job(job_id, progress=0.5, message="running algorithm")
        result_stack = algo.run(stack, **params)

        store.update_job(job_id, progress=0.8, message="writing output")
        output_name = algo.info.output_name
        out_path = RESULT_DIR / f"{job_id}_{output_name}.tif"
        write_stack_array(result_stack, output_name, out_path)

        # Read actual data range from the COG for correct tile rescaling.
        import rasterio as _rio
        stats_min: Optional[float] = None
        stats_max: Optional[float] = None
        try:
            with _rio.open(out_path) as _ds:
                _band = _ds.read(1, masked=True)
                if _band.count() > 0:
                    stats_min = float(_band.min())
                    stats_max = float(_band.max())
        except Exception:
            pass

        # Register the result as a 'result' raster so it can be tiled
        spec = result_stack.spec
        # bounds in WGS84 for the map fit
        from rasterio.warp import transform_bounds
        b = spec.bounds
        b_wgs = transform_bounds(spec.crs, "EPSG:4326", b.left, b.bottom, b.right, b.top)

        # For sweep children, encode the swept param values in the raster name
        # so the user can tell results apart in the side panel.
        job_meta = store.get_job(job_id) or {}
        suffix = f" [{job_meta['sample_label']}]" if job_meta.get("sample_label") else ""
        result_id = store.insert_raster(
            name=f"{algorithm_name}{suffix} of {job_id}",
            cog_path=out_path,
            crs=spec.crs.to_string(),
            bounds=(b.left, b.bottom, b.right, b.top),
            bounds_wgs84=b_wgs,
            resolution=(spec.x_res, spec.y_res),
            width=spec.width,
            height=spec.height,
            dtype="float32",
            nodata=float("nan"),
            kind="result",
            semantic="continuous",
            stats_min=stats_min,
            stats_max=stats_max,
        )

        store.update_job(
            job_id, status="succeeded", progress=1.0, message="done",
            output_id=result_id,
        )

    except Exception as exc:
        tb = traceback.format_exc()
        store.update_job(
            job_id,
            status="failed",
            message=f"{exc.__class__.__name__}: {exc}\n{tb}",
        )
