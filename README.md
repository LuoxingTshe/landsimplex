# LandSimplex

> **LandSimplex: A Simplex-Sampled Weight-Space Sensitivity Prototype for Raster-Based Landscape Suitability Analysis**
>
> **Beta 1.0.2** (`v1.0.2-beta.1`). Research software: single-user, local-only, APIs may still change. See [CHANGELOG.md](CHANGELOG.md).

LandSimplex runs weighted linear combination (WLC) suitability analysis over the whole space of weight choices instead of one hand-picked weight vector. It samples the weight simplex on a uniform lattice, maps how often each pixel stays suitable, and lets you click any pixel to see *which* weight combinations make it pass.

## Scope

| In | Out (later) |
|---|---|
| Upload GeoTIFF → COG (any size, memory-bounded) | Vector data import |
| Catalogue with metadata | Multi-project workspace |
| XYZ tile endpoint via `rio-tiler` | Tile caching |
| Atomic algorithms: `slope`, `aspect`, `weighted_overlay`, `slope_reclassify`, `aspect_reclassify` (tiled, ≤ 2 GiB RAM) | Vector ↔ raster conversion |
| Composite algorithms via `CompositeAlgorithm` base class (e.g. `landscape_sun_score`) | CAD-style editing with snapping |
| Threshold-probability density: internal simplex sweep → WLC → threshold accumulation → [0,1] probability map | |
| Parameter sweep / 区间参数: `list[float]` params via uniform simplex grid (nimplex) with optional per-component `min`/`max` bounds | Scalar range sweeps; Latin-hypercube / adaptive samplers |
| `AlignmentSpec` + `normalize()` for multi-raster alignment | Dynamic LOD beyond what OL gives natively |
| OpenLayers map with layer toggle + delete | Job persistence across server restarts |
| Process-pool jobs with polled progress + per-result stats-driven rescale | |
| Sentinel-2 / Landsat 8/9 bundle ingestion | |
| **Pixel probe + weight-space simplex view**: click a pixel, see every lattice weight vector coloured pass/fail on a simplex (n = 2 segment, n = 3 triangle, n = 4 rotatable tetrahedron) with the lattice pass rate and a live threshold slider | Projection views for n = 5, 6 |
| UI focused on one analysis: `WLC阈值概率密度` (threshold probability); every other algorithm stays in the backend, callable over the API | |
| In-app Quit button that stops backend + frontend dev servers | |

## Prerequisites

- conda or mamba (the env pulls GDAL from conda-forge — saves a lot of pain)
- Node 20+
- [just](https://github.com/casey/just) (optional; commands also given below)

## Setup

macOS (Apple Silicon) from scratch: `brew install just && brew install --cask miniforge`, then `just setup`. The bundled `backend/vendor/nimplex.so` is prebuilt for arm64 + Python 3.11, so Nim is not needed.

```bash
# Backend env (creates conda env 'landplan')
cd backend && conda env create -f environment.yml && cd ..

# Frontend deps
cd frontend && npm install && cd ..
```

Or with just: `just setup`

Tests need pytest (not in `environment.yml`): `conda run -n landplan pip install pytest httpx`, then `cd backend && conda run -n landplan pytest tests/`.

## Run

In two terminals:

```bash
# Terminal 1
cd backend && conda run -n landplan uvicorn app.main:app --reload --host 127.0.0.1 --port 8765

# Terminal 2
cd frontend && npm run dev
```

Or: `just backend` and `just frontend`.

Open <http://localhost:5173>. The **Quit** button at the top right of the sidebar stops both servers.

## Test data

**Quickest: synthetic WLC factors.** No download needed:

```bash
conda run -n landplan python scripts/make_wlc_sample.py
```

This writes four 100×100 factor rasters in [0, 1] to `data/samples/wlc_test/` (gitignored): `A_east` (west→east gradient), `B_north` (south→north gradient), `C_center` (central bump) and `D_waves` (smooth checker of waves). Use A–C as `layer_0..2` for the triangle view, or all four as `layer_0..3` for the tetrahedron (see steps 3 and 5 below).

**A real DEM** for slope / aspect / sun score (API only, see below). Two easy options:

- SRTM 30m tile from <https://search.earthdata.nasa.gov> (NASA Earthdata login)
- For a quick smoke test: any single-band GeoTIFF will work, as long as it has
  a CRS and is north-up. Avoid geographic CRS (lat/lon) — slope numbers will be
  meaningless because dx/dy will be in degrees.

Large files (hundreds of MB uncompressed) are supported — see *Large raster support* below.

## How to verify it works

The UI offers a single algorithm, **WLC阈值概率密度** (`threshold_probability`), preselected in the *Run algorithm* panel. It produces one probability map per job — no per-sample intermediate rasters.

1. **Upload the factors.** Upload `A_east`, `B_north`, `C_center` (and `D_waves`) from `data/samples/wlc_test/`. They appear under "Rasters" within a few seconds. Files of any size are supported; large files (> 200 MiB uncompressed) use the memory-bounded two-step COG path described below.
2. **Check one on the map.** Tick the checkbox. The map should zoom to the raster extent.
3. **Run threshold probability density.** Choose 2–6 rasters (pre-normalised to 0-1) as layers → set `target_score` (e.g. 0.5). Toggle **单纯形采样** to set `T`; the live counter shows how many weight vectors are evaluated. Run. The job internally evaluates WLC for every lattice weight vector and writes a single [0,1] probability map, rendered with a cold→hot colormap (blue = low, red = high).
4. **Constrain the simplex with per-component bounds.** With 单纯形采样 active, each weight row grows a `≥ [0] ≤ [1]` pair. Set e.g. `max[0] = 0.5`; the counter drops to the number of lattice points that survive (the backend's `POST /jobs/preview` returns the exact count). Bounds where `sum(min) > 1`, `sum(max) < 1`, any value outside `[0, 1]`, or where filtering eliminates every lattice point surface inline as red errors and block Run.
5. **Probe a pixel in weight space.** Run on A–C (target 0.5, T = 10 → 66 weight vectors). In the raster list, click **◎** on the result: it becomes the probe target, loads on the map, and the cursor turns into a crosshair. Click the map. A red frame locks the pixel and the **Weight space** view at the top of the sidebar draws the triangle (A, B, C at the vertices): red squares are weight vectors whose score passes the threshold, hollow squares fail. The big number is the lattice pass rate, which equals the map value at that pixel (✓). For the synthetic data: centre ≈ 90.9 %, north-east corner ≈ 68.2 %, south-west corner 0 %. Hover a square for its weights and score; drag the threshold slider to re-evaluate instantly (the readout then notes it differs from the map). `Esc` clears the pixel; click ◎ again to stop probing. With four factors (A–D) the view is a tetrahedron: drag to rotate, double-click to reset; nearer points are larger, hidden edges dashed, min/max bounds drawn as a dashed wireframe. Two factors draw a segment; five or six show the readout only for now.
6. **Delete a layer.** Click the red ✕ button on any raster row in the side panel. The layer is removed from the map, the COG file is deleted from disk, and the catalogue record is removed.
7. **Quit.** Click **Quit** (top right), confirm, and both servers shut down; running jobs are cancelled.

**Other algorithms (API only).** `slope`, `aspect`, `slope_reclassify`, `aspect_reclassify`, `weighted_overlay` and `landscape_sun_score` are still registered (`GET /algorithms`) and run through `POST /jobs`, e.g.

```bash
curl -X POST localhost:8765/jobs -H 'Content-Type: application/json' \
  -d '{"algorithm": "slope_reclassify", "inputs": {"dem": "<raster_id>"}}'
```

A `weighted_overlay` job with `param_ranges.weights = {"kind": "simplex", ...}` is a parameter sweep: one parent plus one result raster per weight vector (≤ 200). Use `threshold_probability` when you only want the probability map.

## What lives where

```
backend/
  app/
    config.py              # paths, port, defaults
    main.py                # FastAPI app; POST /shutdown (Quit button)
    api/
      rasters.py           # upload, list, get, delete — POST /rasters/upload converts to COG
                           #   DELETE /rasters/{id} removes catalogue record + COG file
      tiles.py             # /tiles/{id}/{z}/{x}/{y}.png via rio-tiler
                           #   accepts rescale + colormap query params
                           #   _CUSTOM_CMAPS: "prob-coldhot" (blue→green→red,
                           #   256-entry, threshold_probability auto-routed)
                           #   cmap.register() returns new instance — custom
                           #   maps are looked up inline before rio-tiler builtins
      jobs.py              # /algorithms, /jobs, /jobs/{id}
      scenes.py            # satellite bundle upload + band role assignment
      probe.py             # GET /probe/{result_id}?lon&lat — pixel layer values + weight lattice
                           #   values are warped onto the result grid exactly as normalize() does
    pipeline/
      cogify.py            # GeoTIFF → COG on import
                           #   < 200 MiB: single-pass rio_copy(driver="COG")
                           #   ≥ 200 MiB: tiled GTiff → build_overviews → COG with
                           #   copy_src_overviews=True; GDAL cache capped at 512 MiB
                           #   LOCAL_CS repair: if source CRS lacks a datum (e.g. exported
                           #   from ArcGIS as LOCAL_CS), _repair_local_cs() resolves the
                           #   CRS name via pyproj and patches a temp copy before cogifying
      alignment.py         # AlignmentSpec dataclass (immutable grid contract)
      normalize.py         # load + reproject + align N rasters → AlignedStack
                           #   large-raster path: tiled reproject via rasterio.Band +
                           #   numpy.memmap backing to cap RAM at ~1 MB/tile
      stack.py             # AlignedStack: spec + dict[role→ndarray] + bool mask
                           #   _tmpdir field keeps memmap backing files alive
      writer.py            # AlignedStack → COG via windowed tile writes
                           #   large outputs: build_overviews before COG finalisation
      ingest.py            # archive extraction + band discovery (Sentinel-2 zip, Landsat tar.gz)
    algorithms/
      registry.py          # @register_algorithm decorator + lookup
                           #   AlgorithmInfo schema: fixed InputSpec list,
                           #   optional DynamicInputSpec for variable-N inputs,
                           #   ParamSpec list with declared types, is_composite flag
      composite.py         # CompositeAlgorithm base class (does NOT register itself)
                           #   Helpers: _run_sub(name, stack, **p) to invoke another
                           #   registered algorithm, _reclassify_linear / _reclassify_cosine
                           #   with both small and memmap-tiled paths (NaN preserved)
      builtin/
        slope.py           # Horn's 3x3; tiled path for large DEMs (memmap I/O, ~5 MB peak)
        aspect.py          # gdaldem aspect via osgeo.gdal.DEMProcessing (compass, 0-360°)
                           #   tiled with 1-pixel halo to give GDAL neighbour context
        slope_reclassify.py # slope → linear reclassification → [0,1] score
                           #   reuses slope + CompositeAlgorithm._reclassify_linear
        aspect_reclassify.py # aspect → cosine bell reclassification → [0,1] score
                           #   reuses aspect + CompositeAlgorithm._reclassify_cosine
        weighted_overlay.py# tiled path for large inputs (per-tile weighted sum, ~7 MB peak)
                           #   declares dynamic_inputs(role_prefix="layer_",
                           #   paired_param="weights") so the frontend renders
                           #   the N-layer picker from schema, no hardcoding
        landscape_sun_score.py # composite: aspect + slope → cosine + linear reclass
                           #   → weighted_overlay; output is sun_score ∈ [0,1]
        threshold_probability.py # composite: internal simplex sweep → weighted_overlay
                           #   → threshold → accumulate probability ∈ [0,1]
                           #   dynamic_inputs with paired_param="weights" → frontend simplex UI;
                           #   n_divisions + per-weight min/max bounds from simplex spec
                           #   (forwarded as _internal_sweep by jobs.py interceptor)
    jobs/
      runner.py            # ProcessPoolExecutor wrapper (max_workers=2)
                           #   sweep children: result raster name encodes sample_label
      sweep.py             # parameter-sweep expansion (pure functions, no rasterio)
                           #   parse_param_ranges / sweep_size / expand_ranges
                           #   single kind: simplex (uniform lattice via nimplex,
                           #   C(n+T-1,T) samples; optional per-component min/max bounds
                           #   filter the lattice before children are enqueued)
                           #   MAX_SWEEP_SAMPLES=200 cap enforced at the API layer
    storage/
      local.py             # SQLite catalogue (rasters + jobs tables)
      migrations.py        # incremental schema migrations
  environment.yml
  pyproject.toml           # [project.optional-dependencies] dev = [pytest, httpx]
  tests/                   # pytest suite — `conda run -n landplan pytest tests/`
    conftest.py            # isolated_data_dirs fixture: per-test tmp_path + reload
    test_sweep.py          # parameter sweep: simplex + bounds (pure functions)
    test_jobs_api.py       # /jobs + /jobs/preview (single, sweep, bounds, rejections)
    test_probe.py          # /probe on real pipeline output: pass rate == map value,
                           #   aligned + half-pixel-shifted grids, nodata, 404/400 paths
    test_raster_delete.py, test_reclassify_algorithms.py, test_threshold_probability.py
  vendor/
    nimplex.so             # compiled nimplex Python extension (Nim 2.x, Python 3.11)
                           #   source: https://github.com/amkrajewski/nimplex
                           #   loaded lazily by sweep.py when kind="simplex" is used
  data/                    # auto-created at runtime, gitignored
    cogs/                  # imported source COGs
    results/               # algorithm output COGs
    uploads_tmp/           # staging area for uploads
    bundle_staging/        # extracted satellite archives
    metadata.sqlite

frontend/
  index.html               # all styling lives here: Swiss-style design tokens (CSS variables)
  vite.config.ts           # dev server + POST /__shutdown (Quit button)
  src/
    main.ts                # bootstrap
    api.ts                 # typed fetch wrappers
    map.ts                 # OpenLayers map + helpers (grayscale "basemap" layer class,
                           #   probe cursor layer: pixel footprint + crosshair)
    ui.ts                  # side panel (vanilla DOM; styled via CSS classes, no inline colours)
    simplexView.ts         # weight-space simplex view (SVG) for the pixel probe

scripts/
  make_wlc_sample.py       # synthetic 4-factor WLC test rasters
```

### Interface design

The UI follows the International Typographic (Swiss) Style: black on white, a single signal-red accent (`--red`), Helvetica-family type with hierarchy from size/weight/case only, hairline rules on an 8 px grid, no radius or shadow, and a desaturated basemap so result rasters carry the colour. Edit the `:root` tokens in `frontend/index.html` to retheme.

## Large raster support

The entire pipeline is designed to keep peak Python RAM well below 2 GiB regardless of input size. The threshold for switching to the large-file path is **200 MiB uncompressed per band**.

### Import (`cogify.py`)

For files below the threshold, `rio_copy(driver="COG", overviews="AUTO")` is used directly. For large files, GDAL's COG driver would otherwise compute overviews in `/vsimem/` (virtual RAM), causing OOM. The fix is a three-step approach:

1. Copy the source to an intermediate tiled GeoTIFF on disk. GDAL streams in its own tile buffer — Python never holds the full array. GDAL block cache is capped at 512 MiB via `rasterio.Env(GDAL_CACHEMAX=512)`.
2. Build overviews in-place on the intermediate file. GDAL reads one zoom level at a time; peak RAM ≈ GDAL cache cap.
3. Write the final COG with `copy_src_overviews=True`. GDAL copies the pre-built overviews without recomputing them, so no `/vsimem/` allocation is needed.

Peak disk during conversion ≈ 2.7× uncompressed source size (intermediate tiled GTiff + final COG written before the intermediate is removed).

#### LOCAL_CS repair

Some GIS tools (ArcGIS, older QGIS exports) write the CRS as `LOCAL_CS` — a WKT type that carries no datum, making it impossible to transform bounds to WGS84. `_repair_local_cs()` detects this case, extracts the human-readable name from the WKT (e.g. `"CH1903+ / LV95"`), and queries pyproj to resolve it to a proper EPSG CRS (e.g. EPSG:2056). If resolution succeeds, a temporary GTiff copy of the source is written with the corrected CRS, used as the input to cogify, then deleted. The stored COG and all catalogue metadata use the repaired CRS. Files whose LOCAL_CS name cannot be resolved by pyproj are rejected with a clear 400 error at upload time.

### Normalization (`normalize.py`)

When a reprojected float32 band would exceed 200 MiB:

1. `reproject()` targets a `rasterio.Band` pointing to a temp tiled GeoTIFF. GDAL writes directly; Python-side peak RAM ≈ one GDAL internal tile.
2. The temp GeoTIFF is read back window-by-window into a `numpy.memmap` (raw float32 on disk). Peak RAM ≈ one 512×512 tile ≈ 1 MB.
3. The intermediate GeoTIFF is deleted; only the memmap file remains.
4. The `AlignedStack` holds the memmap arrays. `numpy.memmap` is a subclass of `ndarray`, so algorithms detect large inputs via `isinstance(arr, np.memmap)`.
5. The memmap backing files are stored in a `TemporaryDirectory` referenced by `stack._tmpdir`. They are deleted automatically when the stack is garbage-collected.

### Algorithms (`slope.py`, `aspect.py`, `weighted_overlay.py`)

All atomic algorithms detect a large-raster input with:

```python
isinstance(stack[role], np.memmap) or output_bytes > 200 MiB
```

and switch to a tiled path that processes 512×512 windows:

- **Slope** — reads a (514×514) patch with a 1-pixel halo from the input memmap, runs Horn's 3×3 kernel on it, writes the 512×512 result tile to an output memmap. Peak RAM per tile ≈ 5 × 1 MB = 5 MB.
- **Aspect** — same 1-pixel halo strategy as slope, but the per-tile kernel is `gdal.DEMProcessing(..., "aspect")` running in MEM-driver mode (compass convention, `zeroForFlat=False` → flat areas become NaN). Halo is required so GDAL sees the right neighbour pixels at tile borders. Peak RAM per tile ≈ 5 MB.
- **Weighted overlay** — reads one 512×512 tile from each input layer, accumulates the weighted sum, writes to an output memmap. Peak RAM per tile ≈ (N + 1) × 1 MB (≤ 7 MB for N = 6 layers).

The output memmap is written into the existing `stack._tmpdir` when available, so a single `TemporaryDirectory` keeps both input and output memmap files alive until `write_stack_array()` has persisted the result as a COG.

### Composite algorithms (`composite.py`)

A composite algorithm orchestrates two or more registered algorithms to produce a higher-level analysis without re-implementing their kernels. The base class `CompositeAlgorithm` lives in `algorithms/composite.py`; it does not register itself and is purely a helper layer over `BaseAlgorithm`:

- `_run_sub(name, stack, **params)` — look up another algorithm by registered name and call its `run()`. Output stacks chain naturally via `AlignedStack.with_array`.
- `_reclassify_linear(stack, role, in_lo, in_hi, invert=False)` — remap an array to `[0,1]` by clipping. NaN flows through. For memmap inputs the helper writes its output into the stack's existing `TemporaryDirectory` 512×512 tiles at a time.
- `_reclassify_cosine(stack, role, peak_deg=225.0)` — angular remap `(1 + cos(arr - peak_deg)) / 2`, peaking at `peak_deg` and zeroing 180° away. Same NaN + tiled-memmap contract as `_reclassify_linear`.

`landscape_sun_score` is the canonical example: 27-line `run()` chains `aspect → slope → cosine reclass (SW-favouring) → linear reclass (gentler = higher) → weighted_overlay [0.5, 0.5]`. The final step renames `weighted_overlay`'s `suitability` output back to `sun_score` and attaches it to the **original** input stack, so intermediate roles (`aspect`, `slope`, `layer_0`, `layer_1`, `suitability`) never leak into the writer.

`threshold_probability` demonstrates internal simplex sweep within a composite: its `run()` uses nimplex directly to generate C(n+T-1,T) weight vectors, filters them by optional per-weight-factor `min`/`max` bounds (mirroring the constraints from `sweep.py`), calls `_run_sub("weighted_overlay", ...)` for each survivor, thresholds at `target_score`, and accumulates `1/N` into a probability map. The large-raster path inlines the WLC tile computation to avoid creating a full-size intermediate suitability array per sample. `DynamicInputSpec.paired_param="weights"` reuses the frontend simplex UI (checkbox, T input, per-layer weight inputs, per-weight ≥/≤ bounds) with zero frontend changes. The `jobs.py` interceptor detects `is_composite` and redirects `param_ranges` into `params["_internal_sweep"]` so the simplex sweep stays in-process rather than creating sweep children. The `n_divisions` ParamSpec is removed — T comes from the simplex spec, with a kwarg fallback for backward compat. The output is a single `probability` raster ∈ [0,1]; there are no sweep parent/child jobs.

Frontend exposure: `ui.ts` filters `GET /algorithms` through the `FRONTEND_ALGORITHMS` allowlist (currently only `threshold_probability`, preselected). Each `AlgorithmInfo` still carries `is_composite: bool`; the earlier `基础算法 / 复合分析` tab bar was removed along with the other algorithms' UI.

### Parameter sweep / 区间参数 (`jobs/sweep.py`)

Sweeping is supported only for `list[float]` parameters whose values must sum to 1 (e.g. `weighted_overlay.weights`). The backend generates a uniform lattice on the (n-1)-simplex, creates one parent job (`kind="sweep"`) plus one child per sample (`kind="sweep_child"`), and runs each child through the existing `runner._run_job` path — no algorithm needs sweep-specific code.

**Simplex uniform lattice** (`kind: "simplex"`) — every non-negative weight vector with components stepping by `1/T` and summing to 1. The unfiltered sample count is C(n+T-1, T) where n is the length of the `list[float]` param. Optional per-component `min` / `max` arrays (length n) filter the lattice before child jobs are enqueued:

```json
{
  "kind": "simplex",
  "n_divisions": 4,
  "min": [0.0, 0.1, 0.0],
  "max": [0.5, 0.6, 1.0]
}
```

Bounds validation runs at parse time: each element ∈ [0, 1], `min[i] ≤ max[i]`, and `sum(min) ≤ 1 ≤ sum(max)` (otherwise no weight vector is feasible). Bounds that filter every lattice point return HTTP 400 with a message that surfaces what the user needs to change (raise `n_divisions` or widen). Without explicit bounds, the closed-form binomial gives the count; with bounds, the backend materializes and filters via nimplex (`backend/vendor/nimplex.so`, compiled from https://github.com/amkrajewski/nimplex).

Axes are iterated in `algo.info.params` declaration order — so `sample_index` and result naming stay stable across re-submissions. Multiple simplex axes (e.g. two `list[float]` params) take a Cartesian product.

The expanded sample count is capped at `MAX_SWEEP_SAMPLES = 200`. Larger sweeps return HTTP 400; the frontend shows a live "将运行 N 次" counter that turns red and disables Run at the cap.

**`POST /jobs/preview`** returns the exact post-filter `sample_count` without enqueuing anything. The frontend debounce-calls it whenever bounds are non-default (the closed-form binomial is no longer accurate). Payload is identical to `POST /jobs` minus the `inputs` field. Returns `{ sample_count, max_samples }`; same 400s as the submit path on invalid bounds.

**Single-survivor collapse.** If bounds shrink the sample set to exactly 1, `POST /jobs` runs that one combination as a normal (non-sweep) single job using the surviving weight vector — not the base `params.weights`. The `n == 1` branch sits after `expand_ranges`, so degenerate cases (empty `param_ranges={}`) and bounded cases share the same path.

`GET /jobs/{parent_id}/children` returns the full child list (ordered by `sample_index`) so the side panel can render each child's `sample_label` and a checkbox to overlay its result. Sweep children deliberately do **not** auto-load to the map — the user picks which results to display.

`normalize()` is called once per child. Caching that step across siblings is the obvious optimization but is deliberately deferred — `MAX_SWEEP_SAMPLES + max_workers=2` keep worst-case runtime tolerable, and keeping each child a normal job means the runner / writer / storage code paths are unchanged.

### Pixel probe / weight-space view (`api/probe.py`, `simplexView.ts`)

A pixel's WLC score is linear in the weights: `score(w) = Σ wᵢ·vᵢ`, where `vᵢ` is the pixel's value in layer *i*. So the backend only returns the pixel's *n* layer values plus the lattice, and the frontend gets pass/fail for every weight vector from one dot product each. The threshold slider never calls the backend.

`GET /probe/{raster_id}?lon=&lat=` takes a **result** raster:

- `threshold_probability` result → lattice from `params._internal_sweep.weights` (T, min, max), threshold = `target_score`.
- `weighted_overlay` sweep child → lattice from the parent's `param_ranges.weights`, threshold `null` (set in the UI).
- anything else → 400 `{"code": "not_probeable"}`; a point outside the raster → 404; nodata in any layer → `valid: false`.

Layer values are **sampled on the result grid**, not read from the source COGs: `reproject()` onto a 1×1 destination at the locked pixel, with the same per-role resampling the runner gives `normalize()`. `normalize()` snaps the grid to whole multiples of the resolution, so a result grid can sit half a pixel off its inputs; reading source pixels directly would disagree with the map. The invariant `mean(lattice·values > threshold) == output_value` is tested on aligned and shifted grids, and was checked on 300 random pixels of a 7500×6500 real result.

Response:

```json
{ "mode": "threshold_probability", "layers": [{"role": "layer_0", "name": "A_east", "raster_id": "…"}],
  "pixel": {"row": 50, "col": 50, "footprint": [[lon, lat], …]},
  "valid": true, "values": [0.505, 0.495, 1.0], "lattice": [[1, 0, 0], [0.9, 0.1, 0], …],
  "n_divisions": 10, "min": null, "max": null, "threshold": 0.5, "output_value": 0.909 }
```

In the sidebar, `render()` rebuilds only the area below the simplex view. The view is mounted once, so the 1 s job-poll re-render doesn't reset the SVG or interrupt a slider drag.

### Output (`writer.py`)

The tiled GTiff intermediate is always written window-by-window (no full-array copy). The COG finalisation step applies the same large-file check as `cogify.py`: if the output exceeds 200 MiB, overviews are pre-built before `copy_src_overviews=True` is used, avoiding `/vsimem/` allocation.

### Peak RAM summary

| Stage | Peak RAM |
|---|---|
| Import (large file) | ≤ 512 MiB (GDAL cache cap) |
| Normalize per band | ≈ 1 MB/tile |
| Slope / Aspect (large DEM) | ≈ 5 MB |
| Slope / Aspect reclassify (large) | ≈ 5 MB (governed by parent algo) |
| Weighted overlay (N = 6, large) | ≈ 7 MB |
| Reclassify (composite, large) | ≈ 2 MB/tile |
| Composite chain (sun_score, large) | ≈ 7 MB (governed by the worst sub-step) |
| Threshold probability (N = 6, large) | ≈ 8 MB (accumulator + N layer tiles) |
| Write large output | ≈ 2 MB + 512 MiB GDAL cache |

## Known limitations (beta)

- **Simplex geometry only for n ≤ 4.** Five or six factors show the numeric readout only; projection views (n = 5, 6) are planned.
- **Exact ties at the threshold.** The map is computed in float32, the probe in float64. A weight vector whose score lands exactly on the threshold (possible with coarse class scores like 0.2 / 0.4) can flip, so the probe rate may differ from the map by 1/N.
- **Absolute paths in the catalogue.** `rasters.cog_path` stores absolute paths; moving the project directory breaks existing records (re-upload, or rewrite the path prefix in `metadata.sqlite`).
- **Jobs are not persisted across restarts.** Quit or a crash cancels running jobs; the sidebar only lists jobs submitted in the current session.
- **No reprojection on import.** If you import a raster in EPSG:4326, slope values will be wrong. The `normalize()` function will reproject for multi-raster algorithms.
- **No tile caching.** `rio-tiler` re-reads the COG on every tile request. Fine for one user, will need an LRU on the path to multi-user.
- **No WebSocket.** Frontend polls jobs once per second. WebSocket/SSE push is on the roadmap.
- **No vector data.** Will need GeoPackage support + matching pipeline before adding any vector algorithm.
- **`entry_points`-based plugins not used.** `registry.load_builtins()` only scans the `builtin/` subpackage. Adding a `user_plugins/` scan when the time comes is a 3-line change.
- **Delete is reference-checked.** `DELETE /rasters/{id}` returns 409 if a pending/running job uses the raster as input, or (without `?force=true`) if finished jobs do. Deleting a result raster clears `output_id` on the producing job.

## Architectural choices worth remembering

- **`AlignmentSpec` is frozen and hashable.** Its `signature` is the cache key when we add result memoisation later.
- **Algorithms only see `AlignedStack`.** They never open a raster file. This is enforced by the API surface, not the type system, but easy to police in code review.
- **`InputSpec.semantic` drives per-role resampling at normalize time.** `continuous` → `Resampling.bilinear`, `categorical` → `Resampling.nearest`. The runner builds a `dict[role → Resampling]` from the algorithm's declared schema (including any `DynamicInputSpec.semantic`) and passes it to `normalize()`, so categorical rasters (e.g. land-cover classes) aren't destroyed by linear interpolation. Adding a categorical algorithm is a one-field schema change — no runner or normalize edits.
- **`DynamicInputSpec` declares variable-N inputs in the algorithm schema.** Roles are generated as `{role_prefix}{i}` and an optional `paired_param` names a list-typed param whose length matches the input count (e.g. `weighted_overlay` pairs `layer_i` with `weights[i]`). The frontend renders the N-picker and the paired values straight from the schema — no algorithm-specific frontend branches.
- **COG is the only on-disk raster format.** Imports get cogified, results get cogified. One reader (`rio-tiler`) serves both for tiles, one reader (`rasterio.open`) serves both for compute.
- **`registry.load_builtins()` runs in every worker process.** The pool initializer makes sure each worker has the registry populated before any job runs — otherwise the first job per worker would fail.
- **`numpy.memmap` is the large-file bridge.** It is a proper `ndarray` subclass, so the small-raster and large-raster code paths share the same algorithm kernel. The OS page cache handles which tiles are actually resident in RAM.
- **Algorithm output memmaps share the input's `_tmpdir`.** A single `TemporaryDirectory` reference on the stack keeps all backing files alive until the result COG is written, then everything is cleaned up together on GC. Composite algorithms inherit this: every sub-algorithm and every reclassify helper writes its intermediate memmap into the same tmpdir, so a 4-step pipeline still releases all its scratch files together.
- **Per-result stats drive tile rescale.** When a job writes a result COG, the runner reads its actual min/max via `rasterio.open(...).read(1, masked=True)` and stores them as `stats_min` / `stats_max` on the raster row. The frontend reads these in `addLayer()` and passes them to `/tiles/?rescale=...`, so each new algorithm (slope 0-90, aspect 0-360, sun_score 0-1, ...) renders with its real value range with zero per-algorithm UI tweaks.
- **`CompositeAlgorithm` is a helper class, not a marker.** Subclasses still declare `info.is_composite = True` explicitly, so the schema remains the single source of truth for category/categorisation rather than depending on `isinstance` introspection at API serialisation time.
- **Parameter sweep child = normal job.** A sweep submission produces one parent record plus N children, each a regular row in the `jobs` table with concrete (non-range) params. Children flow through the existing `runner._run_job` path verbatim — no sweep-specific runner, writer, or storage code path. The only runner change is one line that appends `sample_label` to the result raster name so the side panel can tell siblings apart. New algorithms inherit sweep support for free as long as they declare `list[float]` params on `ParamSpec` (only `list[float]` is sweepable, via the simplex grid).
- **DELETE endpoints must return `Response(status_code=204)`, not use `status_code=204` on the decorator.** FastAPI raises an `AssertionError` at startup if a route decorator declares `status_code=204` alongside a Python return-type annotation — even `-> None`. The fix is to omit the status code from the decorator and return `Response(status_code=204)` explicitly; FastAPI does not attach a response model to a raw `Response` object.
- **Custom colormaps bypass rio-tiler's cmap singleton.** `rio_tiler.colormap.cmap.register()` returns a *new* `ColorMaps` instance rather than mutating the module-level singleton, so `cmap.get("custom-name")` fails after registration. Custom colormaps are stored in a module-level `_CUSTOM_CMAPS` dict and checked inline in the tile endpoint before falling through to `cmap_registry.get()` for builtins. The `prob-coldhot` colormap (blue→green→red, 256-entry, generated via `colorsys.hsv_to_rgb`) is the canonical example.
- **Frontend colormap routing by raster name.** `addLayer()` detects special result types by raster-name prefix (e.g. `"threshold_probability"`) and routes them to custom colormaps. This is a lightweight alternative to adding an `algorithm` column to the rasters table for MVP.
- **Zero external services.** SQLite + local files + `ProcessPoolExecutor`; no Redis, Postgres, or S3.
- **LOCAL_CS CRS repair on import.** If a source raster's CRS is a `LOCAL_CS` (no datum — common in ArcGIS exports), `cogify.py` extracts the name from the WKT and queries pyproj to find the matching EPSG CRS. It then writes a temp copy of the source with the corrected CRS and uses that as input to the COG conversion. Patching a COG in place is not used because it breaks GDAL's COG layout optimisation. Files with unresolvable LOCAL_CS are rejected with a 400 at upload time.
