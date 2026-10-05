"""Aspect reclassified to a 0-1 suitability score via cosine bell.

Runs the registered ``aspect`` algorithm, then applies cosine reclassification
so the output is a continuous score in [0, 1] rather than raw degrees.

The score peaks (1.0) at ``peak_deg`` and falls to 0.0 at ±180° from the peak.
Default peak_deg = 225° (south-west) — the most sun-exposed direction in the
northern hemisphere.  Flat areas (NaN from aspect) remain NaN.

Large-raster support is inherited from the underlying aspect (tiled GDAL
DEMProcessing) and CompositeAlgorithm._reclassify_cosine (tiled + memmap).
"""
from __future__ import annotations

from ...pipeline.stack import AlignedStack
from ..composite import CompositeAlgorithm
from ..registry import AlgorithmInfo, InputSpec, ParamSpec, register_algorithm


@register_algorithm
class AspectReclassify(CompositeAlgorithm):
    info = AlgorithmInfo(
        name="aspect_reclassify",
        display="坡向重分类 (Aspect → Score)",
        category="Terrain",
        description=(
            "先计算坡向(0-360°)，再通过余弦钟形函数重分类到[0,1]适宜性评分。"
            "评分在peak_deg方向达到峰值1.0，±180°方向降至0.0。"
            "默认peak_deg=225°(西南向，北半球日照最优)。平坦区域为NaN。"
        ),
        inputs=[
            InputSpec(role="dem", description="数字高程模型", semantic="continuous"),
        ],
        params=[
            ParamSpec(
                name="peak_deg",
                type="float",
                default=225.0,
                description="评分峰值方向(度，罗盘方向)；0=北 90=东 180=南 270=西",
                sweepable=False,
                min=0.0,
                max=360.0,
            ),
        ],
        output_name="aspect_score",
        is_composite=False,  # 基础算法，非复合
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        peak_deg = float(params.get("peak_deg", 225.0))

        # Step 1: run aspect (adds "aspect" array)
        aspect_stack = self._run_sub("aspect", stack)

        # Step 2: reclassify degrees → [0,1] cosine score
        score = self._reclassify_cosine(aspect_stack, "aspect", peak_deg=peak_deg)

        # Return original stack with only the score attached (no "aspect" leakage)
        return stack.with_array(self.info.output_name, score)
