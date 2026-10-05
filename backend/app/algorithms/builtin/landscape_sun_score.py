"""Landscape sun-exposure suitability score.

A composite analysis that chains three registered algorithms:

  1. aspect  — gdaldem aspect via GDAL DEMProcessing (0-360°, NaN for flat)
  2. slope   — Horn's method (0-90°)
  3. reclassify
       aspect → cosine bell peaking at 225° (south-west = 1.0, north-east = 0.0)
       slope  → linear, 0° → 1.0, ≥45° → 0.0 (gentler terrain favoured)
  4. weighted_overlay  — 0.5 * aspect_score + 0.5 * slope_score

Output: a 0-1 suitability raster named "sun_score". NaN flows through where
either aspect (flat areas) or slope was undefined.
"""
from __future__ import annotations

from ...pipeline.stack import AlignedStack
from ..composite import CompositeAlgorithm
from ..registry import AlgorithmInfo, InputSpec, register_algorithm


@register_algorithm
class LandscapeSunScore(CompositeAlgorithm):
    info = AlgorithmInfo(
        name="landscape_sun_score",
        display="景观日照评分 (Landscape Sun Score)",
        category="Landscape",
        description=(
            "DEM → aspect + slope → SW-favouring cosine + gentle-slope linear "
            "reclassification → 0.5/0.5 weighted overlay. Output is a 0-1 "
            "suitability score, higher = more sun-exposed gentle terrain."
        ),
        inputs=[InputSpec(role="dem", description="Digital elevation model", semantic="continuous")],
        params=[],
        output_name="sun_score",
        is_composite=True,
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        s1 = self._run_sub("aspect", stack)              # adds "aspect"
        s2 = self._run_sub("slope", s1)                  # adds "slope"

        aspect_score = self._reclassify_cosine(s2, "aspect", peak_deg=225.0)
        slope_score = self._reclassify_linear(s2, "slope", 0.0, 45.0, invert=True)

        overlay_in = s2.with_array("layer_0", aspect_score).with_array("layer_1", slope_score)
        overlay_out = self._run_sub("weighted_overlay", overlay_in, weights=[0.5, 0.5])

        return stack.with_array(self.info.output_name, overlay_out["suitability"])
