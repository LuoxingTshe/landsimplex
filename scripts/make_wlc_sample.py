"""Generate a synthetic 4-factor WLC test set (100×100, values in [0, 1]).

    conda run -n landplan python scripts/make_wlc_sample.py [out_dir]

Writes to data/samples/wlc_test/ by default (gitignored):
  A_east.tif    factor A: 0 → 1 west to east
  B_north.tif   factor B: 0 → 1 south to north
  C_center.tif  factor C: Gaussian bump, 1 at the centre
  D_waves.tif   factor D: 4×4 checker of smooth waves (use A–D for the n=4 tetrahedron)
  wlc_3band.tif factors A–C as one 3-band file (reference only;
                each algorithm input reads band 1, so upload the single-band files)

EPSG:32632, 10 m pixels, origin on a 10 m multiple so normalize() keeps the grid.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

H = W = 100
CRS = "EPSG:32632"
TRANSFORM = from_origin(400000.0, 5150000.0, 10.0, 10.0)


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    factors = {
        "A_east": x / (W - 1),
        "B_north": 1 - y / (H - 1),
        "C_center": np.exp(-((x - 50) ** 2 + (y - 50) ** 2) / (2 * 25**2)),
        "D_waves": 0.5 + 0.5 * np.sin(2 * np.pi * x / 50) * np.cos(2 * np.pi * y / 50),
    }
    profile = dict(driver="GTiff", height=H, width=W, dtype="float32", crs=CRS, transform=TRANSFORM)
    for name, arr in factors.items():
        with rasterio.open(out_dir / f"{name}.tif", "w", count=1, **profile) as dst:
            dst.write(arr.astype(np.float32), 1)
    with rasterio.open(out_dir / "wlc_3band.tif", "w", count=3, **profile) as dst:
        for i, arr in enumerate(list(factors.values())[:3], start=1):
            dst.write(arr.astype(np.float32), i)
    print(f"wrote {len(factors) + 1} files to {out_dir}")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "samples" / "wlc_test")
