"""Calibration and rectangular-grid placement used by the IBM experiments."""

from collections import deque
import numpy as np
from .geometry import rotated_code, anc_support

VARIANTS = [
    (su, sv, swap) for swap in (False, True) for su in (1, -1) for sv in (1, -1)
]


def qubit_stats(backend):
    """Per-qubit T1/T2 (us) and readout error, tolerant of missing fields."""
    n = backend.num_qubits
    t1 = np.full(n, np.nan)
    t2 = np.full(n, np.nan)
    ro = np.full(n, np.nan)
    try:
        for q, p in enumerate(backend.target.qubit_properties):
            if p is not None:
                if p.t1:
                    t1[q] = p.t1 * 1e6
                if p.t2:
                    t2[q] = p.t2 * 1e6
    except Exception as e:
        print("no qubit_properties:", e)
    try:
        meas = backend.target["measure"]
        for q in range(n):
            props = meas.get((q,))
            if props is not None and props.error is not None:
                ro[q] = props.error
    except Exception as e:
        print("no measure errors:", e)
    return t1, t2, ro


def twoq_edge_errors(backend):
    """{(a, b) sorted: gate error} for every native two-qubit gate edge."""
    err = {}
    for name in backend.target.operation_names:
        try:
            op = backend.target.operation_from_name(name)
            if getattr(op, "num_qubits", 0) != 2:
                continue
            for qargs, props in backend.target[name].items():
                if qargs is None or len(qargs) != 2:
                    continue
                e = getattr(props, "error", None)
                if e is not None:
                    k = tuple(sorted(qargs))
                    err[k] = min(err.get(k, 1.0), float(e))
        except Exception:
            continue
    return err


def infer_grid_coords(num_qubits, edges):
    """(row, col) for every qubit of a rectangular-grid coupling map, from edges alone."""
    adj = {q: set() for q in range(num_qubits)}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)

    def bfs(src):
        dist = {src: 0}
        dq = deque([src])
        while dq:
            u = dq.popleft()
            for v in adj[u]:
                if v not in dist:
                    dist[v] = dist[u] + 1
                    dq.append(v)
        return dist

    corners = [q for q in range(num_qubits) if len(adj[q]) == 2]
    if len(corners) != 4:
        raise ValueError(f"{len(corners)} degree-2 nodes - not a full rectangular grid")
    q0 = corners[0]
    d0 = bfs(q0)
    if len(d0) != num_qubits:
        raise ValueError("coupling map is not connected")
    qX = sorted((c for c in corners if c != q0), key=lambda q: d0[q])[0]
    Cm1 = d0[qX]
    dX = bfs(qX)
    coords = {
        q: ((d0[q] + dX[q] - Cm1) // 2, (d0[q] - dX[q] + Cm1) // 2)
        for q in range(num_qubits)
    }
    if len(set(coords.values())) != num_qubits:
        raise ValueError("coordinate inference collapsed two qubits")
    for a, b in edges:
        (ra, ca), (rb, cb) = coords[a], coords[b]
        if abs(ra - rb) + abs(ca - cb) != 1:
            raise ValueError("inferred coordinates break an edge")
    return coords


def site_uv(i2, j2, var):
    su, sv, swap = var
    u, v = (i2 - j2) // 2, (i2 + j2) // 2  # doubled coords -> integer offsets
    if swap:
        u, v = v, u
    return su * u, sv * v


def all_placements(d, coords):
    """Every (variant, origin) whose whole patch lands on real qubits."""
    data, xa, za = rotated_code(d)
    sites = [(2 * i, 2 * j) for (i, j) in data] + xa + za  # doubled coords throughout
    pos2q = {v: k for k, v in coords.items()}
    NRc = max(r for r, _ in coords.values()) + 1
    NCc = max(c for _, c in coords.values()) + 1
    out = []
    for var in VARIANTS:
        uv = [site_uv(*s, var) for s in sites]
        u0 = min(u for u, _ in uv)
        v0 = min(v for _, v in uv)
        uv = [(u - u0, v - v0) for u, v in uv]
        hu = max(u for u, _ in uv) + 1
        hv = max(v for _, v in uv) + 1
        if hu > NRc or hv > NCc:
            continue
        for r0 in range(NRc - hu + 1):
            for c0 in range(NCc - hv + 1):
                qs = []
                for u, v in uv:
                    q = pos2q.get((r0 + u, c0 + v))
                    if q is None:
                        qs = None
                        break
                    qs.append(q)
                if qs is not None and len(set(qs)) == len(qs):
                    out.append((var, r0, c0, qs, hu, hv))
    return out, sites, len(data)


def select_memory_patch(backend, d):
    edges = {tuple(sorted(edge)) for edge in backend.coupling_map}
    coords = infer_grid_coords(backend.num_qubits, edges)
    candidates, sites, ndata = all_placements(d, coords)
    t1, t2, readout = qubit_stats(backend)
    errors = twoq_edge_errors(backend)
    data, xa, za = rotated_code(d)
    scored = []
    for variant, r0, c0, qubits, _, _ in candidates:
        mapping = dict(zip(sites, qubits))
        score = float(
            np.sum(
                np.nan_to_num(readout[qubits], nan=0.03)
                + 10 / np.maximum(np.nan_to_num(t2[qubits], nan=100), 1)
            )
        )
        for anc in xa + za:
            for i, j in anc_support(*anc, d):
                edge = tuple(sorted((mapping[anc], mapping[(2 * i, 2 * j)])))
                if edge not in edges:
                    score = float("inf")
                    break
                score += 4 * errors.get(edge, 0.01)
        if np.isfinite(score):
            scored.append((score, qubits))
    if not scored:
        raise ValueError("No SWAP-free rotated patch fits this coupling graph.")
    _, qubits = min(scored, key=lambda item: item[0])
    return dict(zip(sites, qubits))


def select_idle_qubits(backend, d, geometric=True, bell=False):
    from scipy.optimize import linear_sum_assignment

    n = 2 * d - 1
    sites = [(r, c) for r in range(n) for c in range(n) if r % 2 == c % 2]
    t1, t2, readout = qubit_stats(backend)
    score = np.nan_to_num(readout, nan=0.03) + 10 / np.maximum(
        np.nan_to_num(t2, nan=100), 1
    )
    if len(sites) > backend.num_qubits:
        raise ValueError("The requested data-qubit count exceeds the backend.")
    edges = {tuple(sorted(edge)) for edge in backend.coupling_map}
    if geometric:
        coords = infer_grid_coords(backend.num_qubits, edges)
        inverse = {v: k for k, v in coords.items()}
        height = max(r for r, c in coords.values()) + 1
        width = max(c for r, c in coords.values()) + 1
        candidates = []
        for r0 in range(height - n + 1):
            for c0 in range(width - n + 1):
                qubits = [inverse[(r0 + r, c0 + c)] for r, c in sites]
                candidates.append((float(np.sum(score[qubits])), qubits))
        if not candidates:
            raise ValueError(
                "The unrotated grid does not fit; calibration placement is a different final-readout diagnostic."
            )
        data = min(candidates, key=lambda item: item[0])[1]
    else:
        data = sorted(int(q) for q in np.argsort(score)[: len(sites)])
    if not bell:
        return data, None
    candidates = sorted(
        {
            q
            for a, b in edges
            for q in (a, b)
            if q not in data and any(tuple(sorted((q, dq))) in edges for dq in data)
        }
    )
    errors = twoq_edge_errors(backend)
    cost = np.full((len(data), len(candidates)), 1e6)
    for i, dq in enumerate(data):
        for j, pq in enumerate(candidates):
            edge = tuple(sorted((dq, pq)))
            if edge in edges:
                cost[i, j] = errors.get(edge, 0.01) + score[pq]
    rows, cols = linear_sum_assignment(cost)
    if len(rows) != len(data) or np.any(cost[rows, cols] >= 1e6):
        raise ValueError(
            "Cannot assign one distinct adjacent Bell partner to every data qubit."
        )
    partners = [None] * len(data)
    for i, j in zip(rows, cols):
        partners[i] = candidates[j]
    return data, partners
