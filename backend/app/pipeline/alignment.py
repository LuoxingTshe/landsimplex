"""AlignmentSpec: the contract that defines a normalized raster grid.

Every algorithm input must arrive on a shared AlignmentSpec. Algorithms must
NEVER accept raw rasters. This dataclass is frozen so it can be hashed and used
as a cache key.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from rasterio.coords import BoundingBox
from rasterio.crs import CRS
from rasterio.transform import Affine

BoundsPolicy = Literal["union", "intersection", "explicit"]


@dataclass(frozen=True)
class AlignmentSpec:
    """Defines a target raster grid: CRS, resolution, origin, extent.

    The grid is fully determined by:
      - crs: target coordinate system
      - x_res, y_res: pixel size (y_res is positive here; we negate when building
        the affine transform so that north-up is the default)
      - origin_x, origin_y: top-left corner of the grid (in target CRS units)
      - width, height: pixel dimensions

    Two specs with identical fields produce the same `signature`, which we use
    as a cache key. Float precision matters: we round to 6 decimal places
    before hashing, otherwise two reprojections that are "theoretically the
    same" might differ in the 12th digit and miss the cache.
    """

    crs_wkt: str          # store as WKT, not CRS object (frozen dataclass needs hashable fields)
    x_res: float
    y_res: float          # always positive
    origin_x: float
    origin_y: float       # top-left y (north edge)
    width: int
    height: int

    @property
    def crs(self) -> CRS:
        return CRS.from_wkt(self.crs_wkt)

    @property
    def transform(self) -> Affine:
        # North-up: y_res is negative in the affine (rows increase southward)
        return Affine(self.x_res, 0.0, self.origin_x, 0.0, -self.y_res, self.origin_y)

    @property
    def bounds(self) -> BoundingBox:
        minx = self.origin_x
        maxy = self.origin_y
        maxx = self.origin_x + self.width * self.x_res
        miny = self.origin_y - self.height * self.y_res
        return BoundingBox(minx, miny, maxx, maxy)

    @property
    def shape(self) -> tuple[int, int]:
        """(height, width) - numpy convention."""
        return (self.height, self.width)

    @property
    def signature(self) -> str:
        payload = {
            "crs": self.crs_wkt,
            "x_res": round(self.x_res, 6),
            "y_res": round(self.y_res, 6),
            "origin_x": round(self.origin_x, 6),
            "origin_y": round(self.origin_y, 6),
            "width": self.width,
            "height": self.height,
        }
        blob = json.dumps(payload, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


def spec_from_raster_meta(
    crs: CRS,
    transform: Affine,
    width: int,
    height: int,
) -> AlignmentSpec:
    """Build an AlignmentSpec from a rasterio dataset's metadata.

    Assumes north-up (transform.e < 0). If it isn't, we'd need to reproject first;
    in MVP we just refuse non-north-up rasters at the cogify stage.
    """
    if transform.e >= 0:
        raise ValueError(
            "Raster is not north-up (transform.e >= 0). "
            "Please reproject before importing."
        )
    return AlignmentSpec(
        crs_wkt=crs.to_wkt(),
        x_res=transform.a,
        y_res=-transform.e,  # store as positive
        origin_x=transform.c,
        origin_y=transform.f,
        width=width,
        height=height,
    )
