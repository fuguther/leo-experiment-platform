"""Contiguous NumPy replay storage used by the learning agents."""
from __future__ import annotations

import numpy as np


class _ReplayBuffer:
    """Contiguous NumPy ring buffer preserving deque(maxlen=...) FIFO ordering."""

    def __init__(self, maxlen: int, state_dim: int, n_actions: int):
        self.maxlen = int(maxlen)
        self.state_dim = int(state_dim)
        self.n_actions = int(n_actions)
        self._cap = min(max(1, self.maxlen), 64)
        self._states = np.empty((self._cap, self.state_dim), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._rewards = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, self.state_dim), dtype=np.float32)
        self._masks = np.empty((self._cap, self.n_actions), dtype=bool)
        self._dones = np.empty((self._cap,), dtype=bool)
        self._pos = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        self._pos = 0
        self._size = 0

    def _grow(self) -> None:
        new_cap = min(self.maxlen, max(self._cap * 2, 1))
        states = np.empty((new_cap, self.state_dim), dtype=np.float32)
        actions = np.empty((new_cap,), dtype=np.int64)
        rewards = np.empty((new_cap,), dtype=np.float32)
        next_states = np.empty((new_cap, self.state_dim), dtype=np.float32)
        masks = np.empty((new_cap, self.n_actions), dtype=bool)
        dones = np.empty((new_cap,), dtype=bool)
        n = self._size
        if n:
            states[:n] = self._states[:n]
            actions[:n] = self._actions[:n]
            rewards[:n] = self._rewards[:n]
            next_states[:n] = self._next_states[:n]
            masks[:n] = self._masks[:n]
            dones[:n] = self._dones[:n]
        self._states = states
        self._actions = actions
        self._rewards = rewards
        self._next_states = next_states
        self._masks = masks
        self._dones = dones
        self._cap = new_cap
        self._pos = n

    def append(self, transition) -> None:
        s, a, r, ns, m, d = transition
        if self._size == self._cap and self._cap < self.maxlen:
            self._grow()
        idx = self._pos
        self._states[idx] = s
        self._actions[idx] = a
        self._rewards[idx] = r
        self._next_states[idx] = ns
        self._masks[idx] = m
        self._dones[idx] = d
        self._pos = (idx + 1) % self._cap
        if self._size < self.maxlen:
            self._size += 1

    def _physical_indices(self, logical_indices: np.ndarray) -> np.ndarray:
        if self._size < self.maxlen:
            return logical_indices
        return (self._pos + logical_indices) % self.maxlen

    def sample_batch(self, logical_indices: np.ndarray):
        phys = self._physical_indices(logical_indices)
        return (
            self._states[phys],
            self._actions[phys],
            self._rewards[phys],
            self._next_states[phys],
            self._masks[phys],
            self._dones[phys],
        )

    def to_arrays(self):
        if self._size == 0:
            return (
                np.empty((0, self.state_dim), dtype=np.float32),
                np.empty((0,), dtype=np.int64),
                np.empty((0,), dtype=np.float32),
                np.empty((0, self.state_dim), dtype=np.float32),
                np.empty((0, self.n_actions), dtype=bool),
                np.empty((0,), dtype=bool),
            )
        order = self._physical_indices(np.arange(self._size, dtype=np.int64))
        return (
            self._states[order],
            self._actions[order],
            self._rewards[order],
            self._next_states[order],
            self._masks[order],
            self._dones[order],
        )

    def __getitem__(self, index: int):
        if index < 0:
            index += self._size
        if index < 0 or index >= self._size:
            raise IndexError("replay index out of range")
        phys = index if self._size < self.maxlen else (self._pos + index) % self.maxlen
        return (
            self._states[phys],
            int(self._actions[phys]),
            float(self._rewards[phys]),
            self._next_states[phys],
            self._masks[phys],
            bool(self._dones[phys]),
        )

    def __iter__(self):
        for i in range(self._size):
            yield self[i]


__all__ = ["_ReplayBuffer"]
