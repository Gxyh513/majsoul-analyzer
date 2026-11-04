"""Reusable contiguous buffers for batched Mortal observations."""
from __future__ import annotations

import numpy as np


class InferenceBatchBuffer:
    """Lazily allocate one fixed-size observation/mask batch and reuse it."""

    def __init__(self, capacity: int):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity 必须是正整数")
        self.capacity = capacity
        self.count = 0
        self._observations: np.ndarray | None = None
        self._masks: np.ndarray | None = None

    def append(self, observation: np.ndarray, mask: np.ndarray) -> bool:
        if self.count >= self.capacity:
            raise OverflowError("推理批次缓冲区已满")
        if not isinstance(observation, np.ndarray) or not isinstance(mask, np.ndarray):
            raise TypeError("observation 和 mask 必须是 NumPy 数组")
        if self._observations is None:
            self._observations = np.empty(
                (self.capacity, *observation.shape), dtype=observation.dtype
            )
            self._masks = np.empty((self.capacity, *mask.shape), dtype=mask.dtype)
        if (observation.shape != self._observations.shape[1:]
                or observation.dtype != self._observations.dtype):
            raise ValueError("observation 形状或类型与当前批次不一致")
        if mask.shape != self._masks.shape[1:] or mask.dtype != self._masks.dtype:
            raise ValueError("mask 形状或类型与当前批次不一致")
        np.copyto(self._observations[self.count], observation, casting="no")
        np.copyto(self._masks[self.count], mask, casting="no")
        self.count += 1
        return self.count >= self.capacity

    def view(self) -> tuple[np.ndarray, np.ndarray]:
        if self.count <= 0 or self._observations is None or self._masks is None:
            raise ValueError("推理批次缓冲区为空")
        return self._observations[:self.count], self._masks[:self.count]

    def clear(self) -> None:
        """Reuse allocated arrays without retaining references to input arrays."""
        self.count = 0
