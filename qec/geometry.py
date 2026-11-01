"""Unit-cell coordinates, boundary masks and correction-to-detector parity."""

import numpy as np

IDX = dict(x0=0, x1=1, x2=2, x3=3, z0=4, z1=5, z2=6, z3=7, x0t=8, x1t=9, z0t=10, z1t=11)
X_STEPS = ["tl", "tr", "bl", "br"]
Z_STEPS = ["tl", "bl", "tr", "br"]
_DIR = {"tl": (-1, -1), "tr": (-1, 1), "bl": (1, -1), "br": (1, 1)}


def lattice(d, rounds):
    """(nr = nc, nt, n_voxels). nt = rounds + 1: R measured rounds plus the
    data-readout round that closes the volume."""
    nr = (d + 3) // 2
    nt = rounds + 1
    return nr, nt, nt * nr * nr


def presence(nt, nr, nc):
    """(8, nt, nr, nc) — numpy port of create_p_arr(). Order D0..D3, X0, X1, Z0, Z1."""
    y = np.zeros((8, nt, nr, nc), np.uint8)
    y[0][:, 1:, 1:] = 1  # D0 at (even i, even j)
    y[1][:, 1:, 1:-1] = 1  # D1 at (even i, odd  j)
    y[2][:, 1:-1, 1:] = 1  # D2 at (odd  i, even j)
    y[3][:, 1:-1, 1:-1] = 1  # D3 at (odd  i, odd  j)
    y[4][:, 1:, 1:-1] = 1  # X0
    y[5][:, :-1, 1:-1] = 1  # X1
    y[6][:, 1:-1, :-1] = 1  # Z0
    y[7][:, 1:-1, 1:] = 1  # Z1
    return y


def _sh(x, dr, dc):
    """x[..., r+dr, c+dc] with zeros outside — the shift_* helpers, one function."""
    y = np.zeros_like(x)
    R, C = x.shape[-2:]
    rs = slice(max(0, -dr), R - max(0, dr))
    rd = slice(max(0, dr), R - max(0, -dr))
    cs = slice(max(0, -dc), C - max(0, dc))
    cd = slice(max(0, dc), C - max(0, -dc))
    y[..., rs, cs] = x[..., rd, cd]
    return y


def space_time_syndrome(e, pres):
    """Parity map edges -> detectors, batched over any leading axes.
    e (..., 12, nt, nr, nc) -> (..., 4, nt, nr, nc). Trailing-axis indexing
    lets one shot and a whole batch use the same operation.

    A space-like edge at slice t fires its stabilisers at t. A time-like (measurement)
    edge at t fires the same stabiliser at t AND t+1. These pairs supply the
    temporal structure used to infer measurement faults."""
    e = e.astype(np.uint8)
    ch = lambda k: e[..., IDX[k], :, :, :]
    o = np.zeros(e.shape[:-4] + (4,) + e.shape[-3:], np.uint8)
    o[..., 0, :, :, :] = ch("z0") ^ ch("z1") ^ ch("z2") ^ ch("z3") ^ ch("x0t")
    o[..., 0, 1:, :, :] ^= ch("x0t")[..., :-1, :, :]
    o[..., 1, :, :, :] = (
        ch("z3")
        ^ _sh(ch("z2"), 0, 1)
        ^ _sh(ch("z1"), 1, 0)
        ^ _sh(ch("z0"), 1, 1)
        ^ ch("x1t")
    )
    o[..., 1, 1:, :, :] ^= ch("x1t")[..., :-1, :, :]
    o[..., 2, :, :, :] = (
        ch("x1") ^ ch("x3") ^ _sh(ch("x0"), 0, 1) ^ _sh(ch("x2"), 0, 1) ^ ch("z0t")
    )
    o[..., 2, 1:, :, :] ^= ch("z0t")[..., :-1, :, :]
    o[..., 3, :, :, :] = (
        ch("x2") ^ ch("x3") ^ _sh(ch("x0"), 1, 0) ^ _sh(ch("x1"), 1, 0) ^ ch("z1t")
    )
    o[..., 3, 1:, :, :] ^= ch("z1t")[..., :-1, :, :]
    return o & pres[4:8]


def rotated_code(d):
    """Rotated surface-code sites in the network coordinate convention.
    data: (i, j) integer sites.   xa / za: ancillas as doubled coords (2i, 2j), odd."""
    data = [(i, j) for i in range(d) for j in range(d)]
    xa, za = [], []
    for i2 in range(-1, 2 * d, 2):  # X stabilisers: (i+j) odd
        for j2 in range(1, 2 * d - 2, 2):
            if ((i2 + j2) // 2) % 2 == 1:
                xa.append((i2, j2))
    for i2 in range(1, 2 * d - 2, 2):  # Z stabilisers: (i+j) even
        for j2 in range(-1, 2 * d, 2):
            if ((i2 + j2) // 2) % 2 == 0:
                za.append((i2, j2))
    return data, xa, za


def anc_support(i2, j2, d):
    """The <= 4 data qubits diagonally adjacent to an ancilla; clipping to the
    d x d data grid is what makes the boundary stabilisers weight 2."""
    return [
        ((i2 + di) // 2, (j2 + dj) // 2)
        for di in (-1, 1)
        for dj in (-1, 1)
        if 0 <= (i2 + di) // 2 < d and 0 <= (j2 + dj) // 2 < d
    ]


def cell_of_anc(i2, j2):
    """ancilla (2i, 2j) -> (slot 0..3 = X0, X1, Z0, Z1 ; r ; c)."""
    u, v = (i2 + 1) // 2, (j2 + 1) // 2
    if u % 2 and v % 2:
        return 0, (u + 1) // 2, (v + 1) // 2  # X0
    if u % 2 == 0 and v % 2 == 0:
        return 1, u // 2, v // 2  # X1
    if u % 2 and v % 2 == 0:
        return 2, (u + 1) // 2, v // 2  # Z0
    return 3, u // 2, (v + 1) // 2


def cell_of_data(i, j):
    """data (i, j) -> (slot 0..3 = D0..D3 ; r ; c)."""
    if i % 2 == 0 and j % 2 == 0:
        return 0, i // 2 + 1, j // 2 + 1  # D0
    if i % 2 == 0:
        return 1, i // 2 + 1, (j + 1) // 2  # D1
    if j % 2 == 0:
        return 2, (i + 1) // 2, j // 2 + 1  # D2
    return 3, (i + 1) // 2, (j + 1) // 2


def cx_layers(d):
    """[[(control_site, target_site), ...] x 4] in doubled-coordinate site labels."""
    data, xa, za = rotated_code(d)
    dataset = {(2 * i, 2 * j) for (i, j) in data}
    layers = []
    for k in range(4):
        layer = []
        for a in xa:
            di, dj = _DIR[X_STEPS[k]]
            n = (a[0] + di, a[1] + dj)
            if n in dataset:
                layer.append((a, n))  # ancilla controls
        for a in za:
            di, dj = _DIR[Z_STEPS[k]]
            n = (a[0] + di, a[1] + dj)
            if n in dataset:
                layer.append((n, a))  # data controls
        touched = [q for pair in layer for q in pair]
        assert len(touched) == len(
            set(touched)
        ), f"d={d} layer {k}: a qubit is used twice - CX ordering is wrong"
        layers.append(layer)
    return layers


def detector_slots(d, R, basis):
    """The (slot, t, r, c) detectors the hardware can actually produce, in the order
    raw extraction and the Stim circuit use them.

    Matching basis (the one the prepared state is an eigenstate of): all nt slices —
    slice 0 compares against the prepared eigenvalue, slices 1..R-1 compare
    consecutive rounds, slice R compares the final round against the data readout.
    Non-matching basis: slices 1..R-1 only. Its round-0 outcome is random, so there
    is no reference to compare it against, and there is no data-derived final value
    either. That is a real, unavoidable difference from the synthetic training
    volumes, where those slices carry a (measurement-error-only) signal."""
    nr, nt, _ = lattice(d, R)
    pres = presence(nt, nr, nr)
    match = (0, 1) if basis == "X" else (2, 3)  # X basis -> X stabilisers matched
    out = []
    for slot in range(4):
        ts = range(nt) if slot in match else range(1, max(1, nt - 1))
        for t in ts:
            for r in range(nr):
                for c in range(nr):
                    if pres[4 + slot, 0, r, c]:
                        out.append((slot, t, r, c))
    return out, match


def edge_mask(d, R, pres=None):
    """Which of the 12 x nvox edges physically exist: space-like where the data qubit
    is present, time-like where the ancilla is present and a round follows."""
    nr, nt, _ = lattice(d, R)
    pres = presence(nt, nr, nr) if pres is None else pres
    m = np.zeros((12, nt, nr, nr), np.uint8)
    m[0:4] = pres[0:4]  # X errors on D0..D3
    m[4:8] = pres[0:4]  # Z errors on D0..D3
    m[8:12] = pres[4:8]  # measurement error on X0, X1, Z0, Z1
    m[8:12, nt - 1] = 0  # slice nt-1 is the data readout, not a round
    return m


def logical_sites(d, basis):
    """The data qubits carrying the observable, and which edge species flips it."""
    if basis == "X":
        return [(i, 0) for i in range(d)], (4, 8)  # logical X, flipped by Z errors
    return [(0, j) for j in range(d)], (0, 4)


def avail_mask(d, R, basis):
    """(4, nt, nr, nc) — the detectors hardware actually produces."""
    nr, nt, _ = lattice(d, R)
    pres = presence(nt, nr, nr)
    m = pres[4:8].copy()
    match = (0, 1) if basis == "X" else (2, 3)
    for s in range(4):
        if s not in match:
            m[s, 0] = 0
            m[s, nt - 1] = 0
    return m
