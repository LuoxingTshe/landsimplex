"""AlignedStack: the unit of computation.

A stack holds one or more named numpy arrays that all share the same
AlignmentSpec. Algorithms operate on AlignedStack -> AlignedStack.

For MVP, arrays live in memory. When data outgrows RAM we'll swap np.ndarray
for dask.array.Array - the AlignedStack interface won't change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from .alignment import AlignmentSpec


@dataclass
class AlignedStack:
    """A set of arrays sharing one grid spec, plus a shared validity mask.

    The mask is True where data is valid (not nodata). Algorithms must respect
    the mask - they should never read or write where mask is False without
    explicit intent. Output stacks should propagate the mask (typically by
    AND-ing with the input masks).

    For large rasters, arrays may be numpy.memmap instances backed by temp
    files on disk. _tmpdir holds a reference to the TemporaryDirectory so the
    files stay alive as long as any stack that references them is alive.
    """

    spec: AlignmentSpec
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    mask: np.ndarray | None = None  # shape == spec.shape, dtype=bool
    _tmpdir: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name, arr in self.arrays.items():
            if arr.shape != self.spec.shape:
                raise ValueError(
                    f"Array '{name}' has shape {arr.shape}, "
                    f"expected {self.spec.shape}"
                )
        if self.mask is None:
            self.mask = np.ones(self.spec.shape, dtype=bool)
        elif self.mask.shape != self.spec.shape:
            raise ValueError(
                f"Mask shape {self.mask.shape} doesn't match spec {self.spec.shape}"
            )

    def __getitem__(self, name: str) -> np.ndarray:
        return self.arrays[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self.arrays)

    def with_array(self, name: str, arr: np.ndarray) -> "AlignedStack":
        """Return a new stack with one additional named array."""
        if arr.shape != self.spec.shape:
            raise ValueError(
                f"Array shape {arr.shape} doesn't match spec {self.spec.shape}"
            )
        new_arrays = {**self.arrays, name: arr}
        return AlignedStack(spec=self.spec, arrays=new_arrays, mask=self.mask, _tmpdir=self._tmpdir)

    def with_mask(self, mask: np.ndarray) -> "AlignedStack":
        return AlignedStack(spec=self.spec, arrays=self.arrays, mask=mask, _tmpdir=self._tmpdir)
