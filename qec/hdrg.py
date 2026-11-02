"""Deterministic hard-decision renormalisation-group (HDRG) decoder.

Based on the square-neighbourhood construction in Gicev et al., arXiv
2110.05854v5 Appendix F (pp. 23--24). This is our implementation, not the
authors' unavailable source and not an adaptive union-find decoder. Paths,
boundary priority and tie-breaking are specified here because Appendix F
does not specify those implementation details.

At scales delta=1,2,4,..., form connected components of currently unresolved
defects with Chebyshev separation <=delta. A component touching a legitimate
absorbing boundary is moved to the same nearest boundary. Otherwise an even
component is moved to its lowest-index member. Neutralised components are
removed before the next scale. Never inspect the actual error configuration.
"""

from __future__ import annotations
from functools import lru_cache
import numpy as np
from scipy.spatial import cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


class HDRGDecoder:
    """Geometry-independent neutral cluster decoder.

    coords: syndrome coordinates in units where nearest check separation=1.
    boundary_distances: minimum fault-edge distance to each valid boundary;
      use infinity when a boundary cannot absorb this defect. Boundaries are
      not graph nodes, so unrelated clusters never merge through a boundary.
    pair_path(i,j), boundary_path(i,b): fault-column IDs whose parity-check
      boundary is respectively {i,j} or {i}. Geometry supplies valid paths.
    """

    def __init__(self, coords, boundary_distances, pair_path, boundary_path, n_faults):
        self.coords = np.asarray(coords, dtype=np.float64)
        self.boundary_distances = np.asarray(boundary_distances, dtype=np.float64)
        if self.coords.ndim != 2 or self.boundary_distances.ndim != 2:
            raise ValueError("Coordinates and boundary distances must be matrices")
        if len(self.coords) != len(self.boundary_distances):
            raise ValueError("Boundary table does not match syndrome geometry")
        if not np.isfinite(self.coords).all() or np.any(self.boundary_distances < 0):
            raise ValueError("Invalid geometry")
        self.pair_path, self.boundary_path = pair_path, boundary_path
        self.n_faults = int(n_faults)
        extent = float(np.max(np.ptp(self.coords, axis=0), initial=0))
        finite = self.boundary_distances[np.isfinite(self.boundary_distances)]
        self.max_delta = max(1.0, extent, float(np.max(finite, initial=0)))

    def decode(self, syndrome, trace=False):
        syndrome = np.asarray(syndrome)
        if syndrome.shape != (len(self.coords),) or not np.isin(syndrome, (0, 1)).all():
            raise ValueError("Expected one binary syndrome vector")
        syndrome = syndrome.astype(np.uint8, copy=False)
        active = np.flatnonzero(syndrome)
        correction = np.zeros(self.n_faults, np.uint8)
        delta = 1
        history = []
        while len(active):
            pairs = cKDTree(self.coords[active]).query_pairs(
                delta, p=np.inf, output_type="ndarray"
            )
            graph = coo_matrix(
                (np.ones(len(pairs), np.uint8), (pairs[:, 0], pairs[:, 1])),
                shape=(len(active), len(active)),
            ).tocsr()
            _, labels = connected_components(graph, directed=False)
            keep = np.ones(len(active), bool)
            # Stable index order supplies deterministic root/member tie breaks.
            ordering = np.argsort(labels, kind="stable")
            cuts = np.r_[
                0, np.flatnonzero(np.diff(labels[ordering])) + 1, len(ordering)
            ]
            neutralised = []
            for a, b in zip(cuts[:-1], cuts[1:]):
                positions = ordering[a:b]
                members = active[positions]
                distances = self.boundary_distances[members]
                minimum = float(np.min(distances, initial=np.inf))
                if minimum <= delta:
                    candidates = np.argwhere(distances == minimum)
                    # Boundary index first, then member index (members sorted).
                    chosen = min(
                        (int(side), int(member)) for member, side in candidates
                    )
                    boundary = chosen[0]
                    for member in members:
                        np.bitwise_xor.at(
                            correction, self.boundary_path(int(member), boundary), 1
                        )
                    reason = f"boundary:{boundary}"
                elif len(members) % 2 == 0:
                    root = int(members[0])
                    for member in members[1:]:
                        np.bitwise_xor.at(
                            correction, self.pair_path(int(member), root), 1
                        )
                    reason = "even"
                else:
                    continue
                keep[positions] = False
                if trace:
                    neutralised.append({"members": members.tolist(), "reason": reason})
            if trace:
                history.append(
                    {
                        "delta": delta,
                        "active": active.tolist(),
                        "neutralised": neutralised,
                    }
                )
            active = active[keep]
            if len(active) and delta >= self.max_delta:
                raise ValueError(
                    "Odd residual charge has no reachable absorbing boundary"
                )
            delta *= 2
        return (correction, history) if trace else correction

    def decode_batch(self, syndromes):
        return np.stack([self.decode(s) for s in np.asarray(syndromes)], axis=0)


class SurfaceHDRG:
    """Unrotated square patch, perfect checks, separate CSS decoding.

    Component 0 corrects X using Z checks at (even,odd) grid sites and the
    left/right boundaries. Component 1 corrects Z using X checks at
    (odd,even) and top/bottom boundaries. Grid width is 2d-1. Pair paths move
    row first then column; boundary paths are straight to the chosen edge.
    """

    def __init__(self, d):
        if d < 3 or d % 2 == 0:
            raise ValueError("Expected odd distance >=3")
        self.d = int(d)
        self.n = 2 * d - 1
        rr, cc = np.indices((self.n, self.n))
        self.sites = np.argwhere((rr + cc) % 2 == 0)
        self.lookup = np.full((self.n, self.n), -1, dtype=np.int32)
        self.lookup[tuple(self.sites.T)] = np.arange(len(self.sites))
        self.checks = [
            np.array(
                [(r, c) for r in range(rp, self.n, 2) for c in range(cp, self.n, 2)],
                np.int32,
            )
            for rp, cp in [(0, 1), (1, 0)]
        ]
        self.decoders = []
        for component, checks in enumerate(self.checks):
            axis = 1 - component
            distances = np.column_stack(
                ((checks[:, axis] + 1) // 2, (self.n - checks[:, axis]) // 2)
            )
            self.decoders.append(
                HDRGDecoder(
                    checks / 2,
                    distances,
                    lambda a, b, k=component: self.pair_path(k, a, b),
                    lambda a, b, k=component: self.boundary_path(k, a, b),
                    len(self.sites),
                )
            )

    @lru_cache(maxsize=131072)
    def pair_path(self, component, a, b):
        start = self.checks[component][a].copy()
        end = self.checks[component][b]
        path = []
        for axis in (0, 1):
            while start[axis] != end[axis]:
                step = 1 if end[axis] > start[axis] else -1
                start[axis] += step
                path.append(int(self.lookup[tuple(start)]))
                start[axis] += step
        if any(i < 0 for i in path):
            raise AssertionError("Path crossed a non-data site")
        return np.array(path, np.int32)

    @lru_cache(maxsize=32768)
    def boundary_path(self, component, member, side):
        pos = self.checks[component][member].copy()
        axis = 1 - component
        step = -1 if side == 0 else 1
        target = -1 if side == 0 else self.n
        path = []
        while pos[axis] != target:
            pos[axis] += step
            path.append(int(self.lookup[tuple(pos)]))
            pos[axis] += step
        if any(i < 0 for i in path):
            raise AssertionError("Boundary path crossed a non-data site")
        return np.array(path, np.int32)

    def decode_batch(self, syndromes):
        return np.stack(
            [decoder.decode_batch(s) for decoder, s in zip(self.decoders, syndromes)],
            axis=1,
        )
