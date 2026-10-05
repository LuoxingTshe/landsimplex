"""XYZ tile endpoint backed by rio-tiler.

For each raster_id we open the COG via rio-tiler.Reader. rio-tiler handles
on-the-fly reprojection to Web Mercator (EPSG:3857) and overview selection
based on the requested zoom level.

The frontend OpenLayers source points at:
  /tiles/{raster_id}/{z}/{x}/{y}.png?rescale=0,4000&colormap=viridis

We accept rescale and colormap as query params so the same endpoint can serve
both raw DEMs and analysis results without needing per-layer config.
"""
from __future__ import annotations

import colorsys
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from rio_tiler.colormap import cmap as cmap_registry
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

from ..storage import local as store

router = APIRouter(prefix="/tiles", tags=["tiles"])

# ---------------------------------------------------------------------------
# Custom colormaps (rio-tiler's cmap registry is immutable at module level,
# so we handle custom names inline in the endpoint).
# ---------------------------------------------------------------------------


def _make_probability_colormap() -> dict[int, tuple[int, int, int, int]]:
    """Build a 256-entry cold→hot colormap for probability density.

    Hue sweep: blue (0.66) → cyan → green → yellow → red (0.0)
    Value ramps from 35 % to 100 % so even the darkest pixels are visible
    against OpenLayers' dark background tiles, and the brightest are not
    washed out against the light basemap.

    Returns a dict mapping 0..255 → (R, G, B, A).
    """
    cmap: dict[int, tuple[int, int, int, int]] = {}
    for i in range(256):
        t = i / 255.0  # 0 .. 1
        # Hue: blue at t=0 → red at t=1 (2/3 → 0 in HSV circle)
        hue = 0.66 * (1.0 - t)
        # Slight saturation dip at the extremes to keep colours natural
        saturation = 0.85 + 0.15 * (1.0 - abs(t - 0.5) * 2.0)
        # Value: dark-but-visible at low end, bright at high end
        value = 0.30 + t * 0.70
        r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
        cmap[i] = (int(r * 255), int(g * 255), int(b * 255), 255)
    return cmap


_CUSTOM_CMAPS: dict[str, dict[int, tuple[int, int, int, int]]] = {
    "prob-coldhot": _make_probability_colormap(),
}

# ---------------------------------------------------------------------------


def _parse_rescale(rescale: Optional[str]) -> Optional[tuple[float, float]]:
    if not rescale:
        return None
    try:
        lo, hi = rescale.split(",")
        return float(lo), float(hi)
    except Exception:
        raise HTTPException(400, f"Bad rescale format: '{rescale}', expected 'min,max'")


@router.get("/{raster_id}/{z}/{x}/{y}.png")
def get_tile(
    raster_id: str,
    z: int,
    x: int,
    y: int,
    rescale: Optional[str] = Query(None, description="min,max for linear stretch"),
    colormap: str = Query("viridis", description="rio-tiler builtin colormap name"),
) -> Response:
    meta = store.get_raster(raster_id)
    if meta is None:
        raise HTTPException(404, f"Raster {raster_id} not found")

    rescale_range = _parse_rescale(rescale)

    try:
        with Reader(meta["cog_path"]) as src:
            img = src.tile(x, y, z)
    except TileOutsideBounds:
        # Return a 204 - frontend interprets as transparent tile
        return Response(status_code=204)
    except Exception as exc:
        raise HTTPException(500, f"Tile read failed: {exc}")

    if rescale_range is not None:
        img.rescale(in_range=(rescale_range,))

    # Look up colormap: check custom maps first, then rio-tiler builtins
    if colormap in _CUSTOM_CMAPS:
        cm = _CUSTOM_CMAPS[colormap]
    else:
        try:
            cm = cmap_registry.get(colormap)
        except Exception:
            raise HTTPException(400, f"Unknown colormap: {colormap}")

    content = img.render(img_format="PNG", colormap=cm)
    return Response(content=content, media_type="image/png")
