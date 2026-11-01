"""Finite reset-based memory circuits and their exact CNN/PCM dictionaries."""

from __future__ import annotations
from collections import deque
from functools import lru_cache
import numpy as np
from scipy.sparse import csc_matrix
import stim, pymatching
from .geometry import *
from .hdrg import HDRGDecoder


class _CircuitText:
    """Batch construction avoids repeated Python/C++ large-target append cost."""

    def __init__(self):
        self.lines = []

    def append(self, name, targets=(), argument=None):
        if argument is not None:
            values = argument if isinstance(argument, list) else [argument]
            name += "(" + ",".join(str(x) for x in values) + ")"
        words = []
        for target in targets:
            if isinstance(target, stim.GateTarget):
                assert target.is_measurement_record_target
                words.append("rec[" + str(target.value) + "]")
            else:
                words.append(str(target))
        self.lines.append(name + (" " + " ".join(words) if words else ""))


def memory_circuit(d, R, basis, pprime):
    """Explicit reset-based eight-step extraction; pprime is depolarization.

    Each extraction: ancilla reset, X-ancilla H, four hook-oriented CX layers,
    X-ancilla H, ancilla measurement. Data idle noise follows each of those
    four non-CX steps. Every idle data OR ancilla qubit in a CX slot receives
    single-qubit depolarization. Active CX gets DEPOLARIZE2.
    Data preparation/readout and X-basis rotations have their own noise.
    This is a fully specified study circuit, not a claim of bitwise identity
    with the paper's training sampler or the no-reset IBM circuit.
    """
    if basis not in ("X", "Z"):
        raise ValueError(basis)
    data, xa, za = rotated_code(d)
    anc = xa + za
    n = {(2 * i, 2 * j): k for k, (i, j) in enumerate(data)}
    n.update({a: len(data) + k for k, a in enumerate(anc)})
    dq = list(range(len(data)))
    aq = [n[a] for a in anc]
    xq = [n[a] for a in xa]
    q1 = 3 * pprime / 4
    q2 = 15 * pprime / 16
    qm = pprime / 2
    schedules = []
    for layer in cx_layers(d):
        pairs = [n[q] for pair in layer for q in pair]
        touched = set(pairs)
        idle = [q for q in range(len(data) + len(anc)) if q not in touched]
        schedules.append((pairs, idle))
    c = _CircuitText()
    c.append("R", dq)
    c.append("X_ERROR", dq, qm)
    if basis == "X":
        c.append("H", dq)
        c.append("DEPOLARIZE1", dq, q1)
    for t in range(R):
        c.append("R", aq)
        c.append("X_ERROR", aq, qm)
        c.append("DEPOLARIZE1", dq, q1)
        c.append("TICK")
        c.append("H", xq)
        c.append("DEPOLARIZE1", xq, q1)
        c.append("DEPOLARIZE1", dq, q1)
        c.append("TICK")
        for pairs, idle in schedules:
            c.append("CX", pairs)
            c.append("DEPOLARIZE2", pairs, q2)
            if idle:
                c.append("DEPOLARIZE1", idle, q1)
            c.append("TICK")
        c.append("H", xq)
        c.append("DEPOLARIZE1", xq, q1)
        c.append("DEPOLARIZE1", dq, q1)
        c.append("TICK")
        c.append("X_ERROR", aq, qm)
        c.append("M", aq)
        c.append("DEPOLARIZE1", dq, q1)
        c.append("TICK")
    if basis == "X":
        c.append("H", dq)
        c.append("DEPOLARIZE1", dq, q1)
    c.append("X_ERROR", dq, qm)
    c.append("M", dq)
    slots, _ = detector_slots(d, R, basis)
    inv = {cell_of_anc(*a): a for a in anc}
    ai = {a: i for i, a in enumerate(anc)}
    di = {q: i for i, q in enumerate(data)}
    total = R * len(anc) + len(data)
    rec = lambda index: stim.target_rec(index - total)
    for slot, t, r, col in slots:
        a = inv[(slot, r, col)]
        k = ai[a]
        if t == 0:
            targets = [rec(k)]
        elif t < R:
            targets = [rec(t * len(anc) + k), rec((t - 1) * len(anc) + k)]
        else:
            targets = [rec((R - 1) * len(anc) + k)] + [
                rec(R * len(anc) + di[q]) for q in anc_support(*a, d)
            ]
        # Coordinates identify model slots and time independently of DEM order.
        c.append("DETECTOR", targets, [float(v) for v in (slot, t, r, col)])
    logical = [(i, 0) for i in range(d)] if basis == "X" else [(0, j) for j in range(d)]
    c.append("OBSERVABLE_INCLUDE", [rec(R * len(anc) + di[q]) for q in logical], 0)
    return stim.Circuit("\n".join(c.lines)), slots


class Geometry3D:
    """Measured-basis rectilinear graph, all R+1 matching detector slices.

    Spatial boundaries absorb charge. Initial/final matching checks are fixed,
    therefore no temporal absorbing boundary is introduced. Other-species
    detectors remain CNN input, but are not the measured logical observable.
    """

    def __init__(self, d, R, basis):
        if d < 3 or d % 2 == 0 or R < 1:
            raise ValueError("odd d>=3 and R>=1 required")
        self.d, self.R, self.basis = d, R, basis
        self.nt = R + 1
        self.nr = (d + 3) // 2
        data, xa, za = rotated_code(d)
        self.data = data
        self.anc = xa if basis == "X" else za
        self.nA = len(self.anc)
        self.nD = d * d
        self.pres = presence(self.nt, self.nr, self.nr)
        self.available = avail_mask(d, R, basis)
        self.edge_mask = edge_mask(d, R, self.pres)
        self.slots = detector_slots(d, R, basis)[0]
        self.slot_arrays = tuple(np.array(self.slots).T)
        self.anc_slots = [cell_of_anc(*a) for a in self.anc]
        self.det_slots = [
            (s, t, r, c) for t in range(self.nt) for s, r, c in self.anc_slots
        ]
        self.det_arrays = tuple(np.array(self.det_slots).T)
        self.n_det = len(self.det_slots)
        self.n_edges = self.nt * self.nD + R * self.nA
        di = {q: k for k, q in enumerate(data)}
        supports = [anc_support(*a, d) for a in self.anc]
        self.incident = [[] for _ in data]
        for a, qs in enumerate(supports):
            for q in qs:
                self.incident[di[q]].append(a)
        self.adj = [[] for _ in self.anc]
        self.boundary_seeds = [[], []]
        for q, endpoints in enumerate(self.incident):
            if len(endpoints) == 2:
                a, b = endpoints
                self.adj[a].append((b, q))
                self.adj[b].append((a, q))
            elif len(endpoints) == 1:
                axis = 1 if basis == "X" else 0
                side = 0 if data[q][axis] == 0 else 1
                assert data[q][axis] in (0, d - 1)
                self.boundary_seeds[side].append((endpoints[0], q))
            else:
                raise AssertionError("Invalid spatial edge incidence")
        for row in self.adj:
            row.sort()
        self.parents = []
        self.bpaths = []
        dist = []
        for seeds in self.boundary_seeds:
            dst = np.full(self.nA, -1, np.int32)
            parent = [None] * self.nA
            queue = deque()
            for a, q in sorted(seeds):
                if dst[a] < 0:
                    dst[a] = 1
                    parent[a] = (-1, q)
                    queue.append(a)
            while queue:
                a = queue.popleft()
                for b, q in self.adj[a]:
                    if dst[b] < 0:
                        dst[b] = dst[a] + 1
                        parent[b] = (a, q)
                        queue.append(b)
            assert np.all(dst > 0)
            dist.append(dst)
            self.bpaths.append(parent)
        coords = np.array(
            [
                ((a[0] + a[1]) // 4, (a[0] - a[1]) // 4, t)
                for t in range(self.nt)
                for a in self.anc
            ]
        )
        self.coords = coords
        self.boundary_distances = np.tile(np.array(dist).T, (self.nt, 1))
        rows = []
        cols = []
        for t in range(self.nt):
            for q, ends in enumerate(self.incident):
                for a in ends:
                    rows.append(t * self.nA + a)
                    cols.append(t * self.nD + q)
        for t in range(R):
            for a in range(self.nA):
                for tt in (t, t + 1):
                    rows.append(tt * self.nA + a)
                    cols.append(self.nt * self.nD + t * self.nA + a)
        self.H = csc_matrix(
            (np.ones(len(rows), np.uint8), (rows, cols)),
            shape=(self.n_det, self.n_edges),
        )
        logical = set(logical_sites(d, basis)[0])
        self.faults = np.zeros(self.n_edges, np.uint8)
        self.faults[: self.nt * self.nD] = np.tile(
            [q in logical for q in data], self.nt
        )
        self.matcher = pymatching.Matching.from_check_matrix(self.H)
        self.hdrg = HDRGDecoder(
            coords,
            self.boundary_distances,
            self.pair_path,
            self.boundary_path,
            self.n_edges,
        )
        self.sp0 = 4 if basis == "X" else 0
        self.logical_cells = [cell_of_data(*q) for q in logical_sites(d, basis)[0]]

    def volume(self, detectors):
        out = np.zeros((len(detectors), 12, self.nt, self.nr, self.nr), np.uint8)
        out[:, :8] = self.pres
        out[
            :,
            8 + self.slot_arrays[0],
            self.slot_arrays[1],
            self.slot_arrays[2],
            self.slot_arrays[3],
        ] = detectors
        return out

    def full_detectors(self, dets4):
        return dets4[(slice(None),) + self.slot_arrays]

    def measured_detectors(self, dets4):
        return dets4[(slice(None),) + self.det_arrays]

    def edge_logical(self, edges):
        value = np.zeros(len(edges), np.uint8)
        for s, r, c in self.logical_cells:
            value ^= np.bitwise_xor.reduce(edges[:, self.sp0 + s, :, r, c], axis=1)
        return value

    def global_decode(self, syndrome, kind):
        correction = (
            self.matcher.decode_batch(syndrome)
            if kind == "mwpm_pcm"
            else self.hdrg.decode_batch(syndrome)
        )
        residual = ((self.H @ correction.T).T.astype(np.uint8) & 1) ^ syndrome
        if residual.any():
            raise AssertionError(kind + " failed to explain matching syndrome")
        return ((correction @ self.faults) & 1).astype(np.uint8)

    @lru_cache(maxsize=8192)
    def spatial_path(self, a, b):
        if a == b:
            return ()
        parent = {a: None}
        queue = deque([a])
        while queue and b not in parent:
            i = queue.popleft()
            for j, q in self.adj[i]:
                if j not in parent:
                    parent[j] = (i, q)
                    queue.append(j)
        path = []
        i = b
        while i != a:
            i, q = parent[i]
            path.append(q)
        return tuple(path)

    def pair_path(self, i, j):
        t, a = divmod(i, self.nA)
        u, b = divmod(j, self.nA)
        temporal = [
            self.nt * self.nD + v * self.nA + a for v in range(min(t, u), max(t, u))
        ]
        spatial = [u * self.nD + q for q in self.spatial_path(a, b)]
        return np.array(temporal + spatial, np.int32)

    def boundary_path(self, i, side):
        t, a = divmod(i, self.nA)
        path = []
        while a >= 0:
            a, q = self.bpaths[side][a]
            path.append(t * self.nD + q)
        return np.array(path, np.int32)
