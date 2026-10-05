"""Normalize a set of rasters into a single AlignedStack.

This is the function that makes "drop in N rasters and run an algorithm" work
regardless of their original CRS, resolution, or extent. The output spec is
either:
  - inferred from the first raster (anchor strategy), or
  - explicitly supplied by the caller.

Bounds policy:
  - "union":        output covers the union of all inputs (default)
  - "intersection": output covers only the overlap
  - "explicit":     caller passes target bounds in spec

Inputs that don't cover the full output get nodata where they're absent.
The shared mask reflects "all inputs valid here".

Memory strategy for large rasters
----------------------------------
When a reprojected band would exceed _LARGE_ARRAY_BYTES (float32), we avoid
allocating the full destination array in Python memory by:

  1. Passing a rasterio.Band as the reproject() destination so GDAL streams
     the reprojected data directly into a temp tiled GeoTIFF without any
     Python-side full-array allocation (peak Python RAM ≈ one GDAL tile).
  2. Reading that GeoTIFF back window-by-window into a numpy.memmap backed by
     a raw temp file (peak Python RAM ≈ one _TILE_SIZE² chunk ≈ 1 MB).
  3. Returning numpy.memmap arrays in the AlignedStack.  memmap IS a subclass
     of ndarray, so all algorithm code works unchanged.
  4. Keeping a TemporaryDirectory reference in the stack so the backing files
     live as long as any reference to the stack remains alive.

Mask computation and the mask array itself are also tiled for large rasters to
keep temporary allocations bounded at O(_TILE_SIZE²) bytes.
"""
from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
import rasterio
from rasterio.warp import Resampling, calculate_default_transform, reproject

from .alignment import AlignmentSpec
from .stack import AlignedStack

BoundsPolicy = Literal["union", "intersection"]

# Resampling can be either a single value applied to all roles, or a dict
# mapping role → Resampling for per-input control (e.g. nearest for categorical).
ResamplingSpec = Resampling | dict[str, Resampling]

# Arrays larger than this threshold use the tiled memmap path.
_LARGE_ARRAY_BYTES = 200 * 1024 * 1024  # 200 MB per float32 band

# Window size for tiled reproject and mask computation.
_TILE_SIZE = 512


def _resampling_for(spec: ResamplingSpec, role: str) -> Resampling:
    if isinstance(spec, dict):
        return spec.get(role, Resampling.bilinear)
    return spec


def _snap_floor(value: float, step: float, anchor: float) -> float:
    """Snap toward minus infinity (for min-side edges)."""
    return anchor + math.floor((value - anchor) / step) * step


def _snap_ceil(value: float, step: float, anchor: float) -> float:
    """Snap toward plus infinity (for max-side edges)."""
    return anchor + math.ceil((value - anchor) / step) * step


def _build_target_spec(
    sources: list[Path],
    target_crs_wkt: str | None,
    target_resolution: tuple[float, float] | None,
    bounds_policy: BoundsPolicy,
) -> AlignmentSpec:
    """Compute a single AlignmentSpec that covers all sources.

    Strategy:
      1. CRS: use target_crs_wkt if given, else first source's CRS.
      2. For each source, compute the bounds that source would occupy after
         reprojection to the target CRS.
      3. Take union or intersection of those bounds.
      4. Resolution: use target_resolution if given, else first source's
         post-reprojection resolution.
      5. Snap bounds outward to the resolution grid, anchored at (0, 0) by
         default. (We could anchor at the first source's origin, but anchoring
         at (0, 0) makes specs predictable across runs.)
    """
    if not sources:
        raise ValueError("normalize() needs at least one source raster")

    # Step 1: resolve target CRS
    if target_crs_wkt is None:
        with rasterio.open(sources[0]) as ds:
            target_crs = ds.crs
    else:
        target_crs = rasterio.crs.CRS.from_wkt(target_crs_wkt)

    # Step 2: project each source's footprint into target CRS
    per_source_bounds: list[tuple[float, float, float, float]] = []
    per_source_res: list[tuple[float, float]] = []
    for path in sources:
        with rasterio.open(path) as ds:
            # calculate_default_transform returns the transform/dims that would
            # result from reprojecting `ds` into target_crs. We use its bounds.
            t, w, h = calculate_default_transform(
                ds.crs, target_crs, ds.width, ds.height, *ds.bounds
            )
            # Derive bounds from the transform & shape
            minx = t.c
            maxy = t.f
            maxx = minx + w * t.a
            miny = maxy + h * t.e  # t.e is negative
            per_source_bounds.append((minx, miny, maxx, maxy))
            per_source_res.append((t.a, -t.e))

    # Step 3: combine bounds
    if bounds_policy == "union":
        minx = min(b[0] for b in per_source_bounds)
        miny = min(b[1] for b in per_source_bounds)
        maxx = max(b[2] for b in per_source_bounds)
        maxy = max(b[3] for b in per_source_bounds)
    else:  # intersection
        minx = max(b[0] for b in per_source_bounds)
        miny = max(b[1] for b in per_source_bounds)
        maxx = min(b[2] for b in per_source_bounds)
        maxy = min(b[3] for b in per_source_bounds)
        if minx >= maxx or miny >= maxy:
            raise ValueError("Intersection of input bounds is empty.")

    # Step 4: target resolution
    if target_resolution is None:
        # Use the first source's resolution after reprojection
        x_res, y_res = per_source_res[0]
    else:
        x_res, y_res = target_resolution

    # Step 5: snap bounds outward to grid (anchored at 0)
    # Min edges floor (move outward = away from data), max edges ceil.
    snap_minx = _snap_floor(minx, x_res, 0.0)
    snap_miny = _snap_floor(miny, y_res, 0.0)
    snap_maxx = _snap_ceil(maxx, x_res, 0.0)
    snap_maxy = _snap_ceil(maxy, y_res, 0.0)

    width = int(round((snap_maxx - snap_minx) / x_res))
    height = int(round((snap_maxy - snap_miny) / y_res))

    return AlignmentSpec(
        crs_wkt=target_crs.to_wkt(),
        x_res=x_res,
        y_res=y_res,
        origin_x=snap_minx,
        origin_y=snap_maxy,
        width=width,
        height=height,
    )


def _reproject_to_array(
    src_path: Path,
    spec: AlignmentSpec,
    nodata_fill: float,
    resampling: Resampling,
    tmp_root: Path,
    role: str,
) -> np.ndarray:
    """Reproject src_path onto spec. Returns float32 array (may be a memmap).

    Small rasters (< _LARGE_ARRAY_BYTES): plain in-memory ndarray.
    Large rasters: GDAL streams into a temp tiled GeoTIFF via rasterio.Band
    (no Python-side full-array allocation), then the data is copied window by
    window into a disk-backed numpy.memmap.  Peak Python RAM ≈ one tile (~1 MB).
    """
    n_bytes = spec.height * spec.width * 4  # float32

    if n_bytes <= _LARGE_ARRAY_BYTES:
        dst = np.full(spec.shape, nodata_fill, dtype=np.float32)
        with rasterio.open(src_path) as ds:
            reproject(
                source=rasterio.band(ds, 1),
                destination=dst,
                src_transform=ds.transform,
                src_crs=ds.crs,
                src_nodata=ds.nodata,
                dst_transform=spec.transform,
                dst_crs=spec.crs,
                dst_nodata=nodata_fill,
                resampling=resampling,
            )
        return dst

    # --- Large-raster path ---------------------------------------------------
    # Step 1: reproject into a tiled temp GeoTIFF using rasterio.Band as the
    # destination.  GDAL writes in its own internal tile buffer; Python never
    # holds the full array.
    tif_path = tmp_root / f"{role}.tif"
    mm_path = tmp_root / f"{role}.mm"

    profile = {
        "driver": "GTiff",
        "dtype": "float32",
        "width": spec.width,
        "height": spec.height,
        "count": 1,
        "crs": spec.crs,
        "transform": spec.transform,
        "nodata": nodata_fill,
        "tiled": True,
        "blockxsize": _TILE_SIZE,
        "blockysize": _TILE_SIZE,
    }
    with rasterio.open(src_path) as src_ds:
        with rasterio.open(tif_path, "w", **profile) as dst_ds:
            reproject(
                source=rasterio.band(src_ds, 1),
                destination=rasterio.band(dst_ds, 1),
                src_nodata=src_ds.nodata,
                dst_nodata=nodata_fill,
                resampling=resampling,
            )

    # Step 2: copy from the tiled GeoTIFF into a memmap window by window so
    # peak RAM stays at O(_TILE_SIZE²) ≈ 1 MB.
    mm = np.memmap(mm_path, dtype="float32", mode="w+", shape=spec.shape)
    with rasterio.open(tif_path) as ds:
        for _, window in ds.block_windows(1):
            row_off = int(window.row_off)
            col_off = int(window.col_off)
            tile = ds.read(1, window=window)
            mm[row_off : row_off + tile.shape[0], col_off : col_off + tile.shape[1]] = tile
    mm.flush()

    tif_path.unlink()  # intermediate GeoTIFF no longer needed; keep only the memmap
    return mm


def _update_combined_mask(
    combined_mask: np.ndarray,
    dst: np.ndarray,
    nodata_fill: float,
    spec: AlignmentSpec,
) -> None:
    """AND combined_mask in-place with the validity mask of dst.

    Processes dst in _TILE_SIZE² windows so that neither a full-sized temporary
    bool array nor the full dst needs to be in RAM at once (important when dst
    is a large memmap).
    """
    for row_off in range(0, spec.height, _TILE_SIZE):
        h = min(_TILE_SIZE, spec.height - row_off)
        for col_off in range(0, spec.width, _TILE_SIZE):
            w = min(_TILE_SIZE, spec.width - col_off)
            tile = dst[row_off : row_off + h, col_off : col_off + w]
            if math.isnan(nodata_fill):
                valid = ~np.isnan(tile)
            else:
                valid = tile != nodata_fill
            combined_mask[row_off : row_off + h, col_off : col_off + w] &= valid


def normalize(
    sources: dict[str, Path],
    *,
    target_crs_wkt: str | None = None,
    target_resolution: tuple[float, float] | None = None,
    bounds_policy: BoundsPolicy = "union",
    resampling: ResamplingSpec = Resampling.bilinear,
    nodata_fill: float = float("nan"),
) -> AlignedStack:
    """Load sources, reproject each onto a common grid, return AlignedStack.

    `sources` maps role names ("dem", "slope_input", "weight_1"...) to file paths.
    Role names become array keys in the returned stack.

    `resampling` is either a single Resampling applied to every input, or a
    dict[role → Resampling] for per-role control.  The job runner derives the
    dict from the algorithm's declared semantics (continuous→bilinear,
    categorical→nearest) so categorical rasters aren't destroyed by linear
    interpolation.

    All arrays come out as float32 with nodata = nodata_fill (default NaN).
    Mask is True where ALL inputs are valid.

    For large rasters the returned stack's arrays are numpy.memmap instances
    backed by temp files.  The files are kept alive by stack._tmpdir and are
    deleted automatically when the stack (and any derived stacks) are GC'd.
    """
    paths = list(sources.values())
    spec = _build_target_spec(
        paths, target_crs_wkt, target_resolution, bounds_policy
    )

    # TemporaryDirectory holds memmap backing files for large-raster bands.
    # We store it in the returned AlignedStack so the files live as long as
    # the stack (and any with_array / with_mask copies of it) are alive.
    tmpdir = tempfile.TemporaryDirectory(prefix="landplan_norm_")
    tmp_root = Path(tmpdir.name)

    n_bytes = spec.height * spec.width  # bool mask = 1 byte/pixel
    if n_bytes > _LARGE_ARRAY_BYTES:
        cm_path = tmp_root / "combined_mask.mm"
        combined_mask: np.ndarray = np.memmap(cm_path, dtype=bool, mode="w+", shape=spec.shape)
        combined_mask[:] = True
    else:
        combined_mask = np.ones(spec.shape, dtype=bool)

    stack = AlignedStack(spec=spec, _tmpdir=tmpdir)

    for role, path in sources.items():
        role_resampling = _resampling_for(resampling, role)
        dst = _reproject_to_array(path, spec, nodata_fill, role_resampling, tmp_root, role)
        _update_combined_mask(combined_mask, dst, nodata_fill, spec)
        stack = stack.with_array(role, dst)

    return stack.with_mask(combined_mask)
