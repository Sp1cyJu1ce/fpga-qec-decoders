"""Compiled 2D implementation of the exact conventions in hdrg.SurfaceHDRG.

The BFS below computes fixed-scale connected components. It does not change
the decoder into adaptive union-find. The clear Python/SciPy implementation
remains the reference; require exact correction-bit equality before use.
"""

from __future__ import annotations
import numpy as np

try:
    from numba import njit
except ImportError:

    def njit(*args, **kwargs):
        def missing(fn):
            def unavailable(*a, **k):
                raise ImportError("Install Numba to use compiled HDRG")

            return unavailable

        return missing


@njit(cache=True)
def _decode_batch(syndromes, d, component, lookup):
    batch, ns = syndromes.shape
    n = 2 * d - 1
    height = d - component
    width = d - 1 + component
    correction = np.zeros((batch, d * d + (d - 1) * (d - 1)), np.uint8)
    for shot in range(batch):
        active = syndromes[shot].copy()
        delta = 1
        labels = np.empty(ns, np.int32)
        queue = np.empty(ns, np.int32)
        while np.any(active):
            labels[:] = -1
            for first in range(ns):
                if not active[first] or labels[first] >= 0:
                    continue
                labels[first] = first
                queue[0] = first
                head = 0
                tail = 1
                while head < tail:
                    u = queue[head]
                    head += 1
                    ur = u // width
                    uc = u % width
                    for vr in range(max(0, ur - delta), min(height, ur + delta + 1)):
                        for vc in range(max(0, uc - delta), min(width, uc + delta + 1)):
                            v = vr * width + vc
                            if active[v] and labels[v] < 0:
                                labels[v] = first
                                queue[tail] = v
                                tail += 1
                nearest = d + 1
                side = -1
                for k in range(tail):
                    member = queue[k]
                    coordinate = member % width if component == 0 else member // width
                    left = coordinate + 1
                    right = d - 1 - coordinate
                    if left < nearest or (left == nearest and side > 0):
                        nearest = left
                        side = 0
                    if right < nearest:
                        nearest = right
                        side = 1
                touches = nearest <= delta
                if not touches and tail % 2:
                    continue
                # Lowest syndrome index, independent of BFS discovery order.
                root = first
                for k in range(tail):
                    member = queue[k]
                    r = 2 * (member // width) + component
                    c = 2 * (member % width) + (1 - component)
                    if touches:
                        step = -1 if side == 0 else 1
                        target = -1 if side == 0 else n
                        if component == 0:
                            while c != target:
                                c += step
                                correction[shot, lookup[r, c]] ^= 1
                                c += step
                        else:
                            while r != target:
                                r += step
                                correction[shot, lookup[r, c]] ^= 1
                                r += step
                    elif member != root:
                        rr = 2 * (root // width) + component
                        cc = 2 * (root % width) + (1 - component)
                        while r != rr:
                            step = 1 if rr > r else -1
                            r += step
                            correction[shot, lookup[r, c]] ^= 1
                            r += step
                        while c != cc:
                            step = 1 if cc > c else -1
                            c += step
                            correction[shot, lookup[r, c]] ^= 1
                            c += step
                    active[member] = 0
            delta *= 2
    return correction


class FastSurfaceHDRG:
    def __init__(self, d):
        if d < 3 or d % 2 == 0:
            raise ValueError("Expected odd distance >=3")
        self.d = int(d)
        n = 2 * d - 1
        rr, cc = np.indices((n, n))
        sites = np.argwhere((rr + cc) % 2 == 0)
        self.lookup = np.full((n, n), -1, np.int32)
        self.lookup[tuple(sites.T)] = np.arange(len(sites))

    def decode_batch(self, syndromes):
        if len(syndromes) != 2:
            raise ValueError("Expected X-error and Z-error syndrome matrices.")
        arrays = [np.asarray(s) for s in syndromes]
        checks = self.d * (self.d - 1)
        if any(
            s.ndim != 2 or s.shape[1] != checks or not np.isin(s, (0, 1)).all()
            for s in arrays
        ) or len(arrays[0]) != len(arrays[1]):
            raise ValueError(
                "Syndromes must be binary batches with one column per check."
            )
        return np.stack(
            [
                _decode_batch(
                    np.ascontiguousarray(s, dtype=np.uint8), self.d, i, self.lookup
                )
                for i, s in enumerate(arrays)
            ],
            axis=1,
        )
