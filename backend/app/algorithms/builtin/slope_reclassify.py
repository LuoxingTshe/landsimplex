"""Slope reclassified to a 0-1 suitability score.

Runs the registered ``slope`` algorithm, then applies linear reclassification
so the output is a continuous score in [0, 1] rather than raw degrees.

By default gentler slopes score higher (invert=True): 0° maps to 1.0 and
everything ≥ in_hi maps to 0.0.  Set invert to 0 to reverse the direction.

Large-raster support is inherited from the underlying slope (tiled Horn) and
CompositeAlgorithm._reclassify_linear (tiled + memmap).
"""
from __future__ import annotations

from ...pipeline.stack import AlignedStack
from ..composite import CompositeAlgorithm
from ..registry import AlgorithmInfo, InputSpec, ParamSpec, register_algorithm


@register_algorithm
class SlopeReclassify(CompositeAlgorithm):
    info = AlgorithmInfo(
        name="slope_reclassify",
        display="坡度重分类 (Slope → Score)",
        category="Terrain",
        description=(
            "先计算坡度(度)，再线性重分类到[0,1]适宜性评分。"
            "默认越平缓评分越高：in_lo°→1.0，≥in_hi°→0.0。"
        ),
        inputs=[
            InputSpec(role="dem", description="数字高程模型", semantic="continuous"),
        ],
        params=[
            ParamSpec(
                name="in_lo",
                type="float",
                default=0.0,
                description="输入下限(度)；对应评分极值（默认=高分端）",
                sweepable=False,
                min=0.0,
                max=90.0,
            ),
            ParamSpec(
                name="in_hi",
                type="float",
                default=45.0,
                description="输入上限(度)；对应评分极值（默认=低分端）",
                sweepable=False,
                min=0.0,
                max=90.0,
            ),
            ParamSpec(
                name="invert",
                type="int",
                default=1,
                description="反转：1=越平缓分越高，0=越陡峭分越高",
                sweepable=False,
                min=0,
                max=1,
            ),
        ],
        output_name="slope_score",
        is_composite=False,  # 基础算法，非复合
    )

    def run(self, stack: AlignedStack, **params) -> AlignedStack:
        in_lo = float(params.get("in_lo", 0.0))
        in_hi = float(params.get("in_hi", 45.0))
        invert = bool(int(params.get("invert", 1)))

        if in_hi == in_lo:
            raise ValueError("in_hi must differ from in_lo")

        # Step 1: run slope (adds "slope" array)
        slope_stack = self._run_sub("slope", stack)

        # Step 2: reclassify degrees → [0,1] score
        score = self._reclassify_linear(slope_stack, "slope", in_lo, in_hi, invert=invert)

        # Return original stack with only the score attached (no "slope" leakage)
        return stack.with_array(self.info.output_name, score)
