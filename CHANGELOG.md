# Changelog

## v1.0.0-beta.1 — 2026-10-08

First usable beta of LandSimplex.

### Added
- **Pixel probe + weight-space simplex view.** Mark a `threshold_probability` result (or a `weighted_overlay` sweep child) with ◎, click the map, and the sidebar draws the weight simplex for that pixel: every lattice weight vector coloured pass/fail, the lattice pass rate (equal to the map value), per-layer values, score range, and a threshold slider that re-evaluates locally. n = 2 draws a segment, n = 3 a triangle.
- `GET /probe/{raster_id}?lon&lat`: pixel layer values sampled on the result grid exactly as `normalize()` does, plus the lattice, bounds and threshold.
- In-app **Quit** button: `POST /shutdown` (backend, stops the uvicorn reloader and kills the job pool) and `POST /__shutdown` (Vite dev server). Both reject foreign `Origin` headers.
- `scripts/make_wlc_sample.py`: synthetic 3-factor test rasters, so the probe can be tried without downloading data.
- Swiss-style interface (single red accent, Helvetica, 8 px grid, desaturated basemap).

### Changed
- The sidebar re-renders only below the simplex view, so job polling no longer resets the view or interrupts slider drags.
- `.gitignore` blocks all case/sample geodata (`data/`, `*.tif`, `*.gpkg`, shapefiles, archives, `*.sqlite`).

### Known limitations
- Simplex geometry for n ≥ 4 factors is not drawn yet (readout only).
- Probe pass rate can differ from the map by 1/N at exact float ties with the threshold.
- `cog_path` is stored as an absolute path; moving the project breaks existing catalogue records.
- Jobs are not persisted across restarts.

## v0.1.0

Research prototype: GeoTIFF → COG import (memory-bounded for large files), XYZ tiles, slope / aspect / reclassify / weighted overlay / landscape sun score, simplex parameter sweeps with bounds (nimplex), threshold-probability density, Sentinel-2 / Landsat bundle ingestion, reference-checked raster deletion.
