"""Algorithm registry.

Each algorithm is a class with declared inputs, params, and a run() method.
Algorithms register themselves via @register_algorithm at import time.

The registry exposes:
  - get(name): retrieve an algorithm class
  - list_all(): for the API to publish to the frontend

The frontend uses the declared schema to auto-generate the run dialog -
adding a new algorithm requires zero frontend changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..pipeline.stack import AlignedStack


@dataclass
class InputSpec:
    """Declared input for an algorithm.

    role:      key under which the array appears in AlignedStack.arrays
    semantic:  "continuous" | "categorical" - drives per-role resampling in
               normalize() (continuous→bilinear, categorical→nearest)
    """
    role: str
    description: str = ""
    semantic: str = "continuous"


@dataclass
class DynamicInputSpec:
    """Declares an algorithm that accepts a variable number of inputs.

    Generated roles are `{role_prefix}{i}` for i in 0..N-1 (e.g. "layer_0",
    "layer_1", ...). N is chosen by the user at submit time, bounded by
    [min_count, max_count].

    If `paired_param` is set, that param must be a list whose length equals
    the input count; the frontend renders one value next to each dynamic input.

    The semantic field drives per-role resampling for every generated role.
    """
    role_prefix: str
    description: str = ""
    semantic: str = "continuous"
    min_count: int = 1
    max_count: int = 16
    paired_param: str | None = None


@dataclass
class ParamSpec:
    name: str
    type: str  # "float" | "int" | "str" | "list[float]"
    default: Any
    description: str = ""
    # Sweep metadata (optional; consumed by jobs.sweep + the frontend UI).
    # sweepable: if False, the frontend hides the [区间] toggle for this param.
    # min / max / step_hint: pre-filled bounds for the UI; they are NOT enforced
    # by the sweep parser, which only validates user-supplied min/max/step.
    sweepable: bool = True
    min: float | None = None
    max: float | None = None
    step_hint: float | None = None


@dataclass
class AlgorithmInfo:
    name: str
    display: str
    category: str
    description: str
    inputs: list[InputSpec] = field(default_factory=list)
    params: list[ParamSpec] = field(default_factory=list)
    output_name: str = "output"
    dynamic_inputs: DynamicInputSpec | None = None
    is_composite: bool = False


class BaseAlgorithm:
    info: AlgorithmInfo

    def run(self, stack: AlignedStack, **params: Any) -> AlignedStack:
        """Run the algorithm. Receives a fully normalized AlignedStack.

        Returns a new AlignedStack with the result under self.info.output_name.
        Implementations should never read non-normalized rasters.
        """
        raise NotImplementedError


_REGISTRY: dict[str, type[BaseAlgorithm]] = {}


def register_algorithm(cls: type[BaseAlgorithm]) -> type[BaseAlgorithm]:
    """Decorator: register an algorithm class."""
    if not hasattr(cls, "info"):
        raise TypeError(f"{cls.__name__} must declare `info: AlgorithmInfo`")
    if cls.info.name in _REGISTRY:
        raise ValueError(f"Algorithm '{cls.info.name}' already registered")
    _REGISTRY[cls.info.name] = cls
    return cls


def get(name: str) -> type[BaseAlgorithm]:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown algorithm: {name}")
    return _REGISTRY[name]


def list_all() -> list[AlgorithmInfo]:
    return [cls.info for cls in _REGISTRY.values()]


def load_builtins() -> None:
    """Import every module under builtin/ so their @register_algorithm runs."""
    from . import builtin  # noqa: F401
    import importlib
    import pkgutil

    for _, modname, _ in pkgutil.iter_modules(builtin.__path__):
        importlib.import_module(f"{builtin.__name__}.{modname}")
