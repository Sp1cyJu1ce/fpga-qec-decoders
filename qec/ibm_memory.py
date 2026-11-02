"""No-reset memory circuit and IBM raw-measurement conversion."""

import numpy as np
import stim
from .geometry import *

MED = {}
PREP_BITS = {}
SEED = 0  # Used only when building a new circuit; saved records carry preparation bits.


def configure_manifest(manifest):
    MED.clear()
    MED.update(manifest["medians"])
    PREP_BITS.clear()
    for cfg in manifest["configs"]:
        key = (cfg["d"], cfg["prep"])
        bits = np.array([int(v) for v in cfg["prep_bits"]], np.uint8)
        if key in PREP_BITS:
            assert np.array_equal(PREP_BITS[key], bits)
        PREP_BITS[key] = bits


def prep_pattern(d, prep):
    key = (d, prep)
    if key not in PREP_BITS:
        if prep in ("zeros", "plus"):
            PREP_BITS[key] = np.zeros(d * d, np.uint8)
        elif prep in ("ones", "minus"):
            PREP_BITS[key] = np.ones(d * d, np.uint8)
        elif prep == "random":
            PREP_BITS[key] = (
                np.random.default_rng(SEED + d).integers(0, 2, d * d).astype(np.uint8)
            )
        else:
            raise ValueError(prep)
    return PREP_BITS[key].copy()


def creg_layout(d, R):
    """The classical register, in the order it is written: R rounds of ancillas
    (X ancillas then Z ancillas, matching rotated_code), then the data readout."""
    data, xa, za = rotated_code(d)
    anc = xa + za
    return anc, data, len(anc) * R + len(data)


def stim_circuit(cfg, p1=None, p2=None, pm=None, pd=None):
    """stim mirror of build_circuit(), detectors in detector_slots() order.
    Noise defaults come from the device calibration snapshot."""
    d, R, basis = cfg["d"], cfg["rounds"], cfg["basis"]
    p1 = MED["e2q"] / 10 if p1 is None else p1  # 1q gate depolarising
    p2 = MED["e2q"] if p2 is None else p2  # 2q gate depolarising
    pm = MED["ro"] if pm is None else pm  # ancilla readout flip
    pd = MED["ro"] if pd is None else pd  # data readout flip
    anc, data, nbits = creg_layout(d, R)
    layers = cx_layers(d)
    bits = prep_pattern(d, cfg["prep"])
    n = {}  # site -> stim qubit index
    for k, (i, j) in enumerate(data):
        n[(2 * i, 2 * j)] = k
    for k, a in enumerate(anc):
        n[a] = len(data) + k
    dq = [n[(2 * i, 2 * j)] for (i, j) in data]
    xq = [n[a] for a in rotated_code(d)[1]]
    aq = [n[a] for a in anc]
    nq = len(data) + len(anc)

    sc = stim.Circuit()
    sc.append("R", range(nq))
    sc.append("X", [q for q, b in zip(dq, bits) if b])
    if basis == "X":
        sc.append("H", dq)
    sc.append("DEPOLARIZE1", range(nq), p1)
    for rep in range(R):
        sc.append("H", xq)
        sc.append("DEPOLARIZE1", xq, p1)
        sc.append("TICK")
        for layer in layers:
            pairs = [q for (cs, ts) in layer for q in (n[cs], n[ts])]
            sc.append("CX", pairs)
            sc.append("DEPOLARIZE2", pairs, p2)
            idle = [q for q in range(nq) if q not in set(pairs)]
            if idle:
                sc.append("DEPOLARIZE1", idle, p1)
            sc.append("TICK")
        sc.append("H", xq)
        sc.append("DEPOLARIZE1", xq, p1)
        # Readout error as a STATE flip immediately before M, not a classical
        # misreport -- and in the no-reset scheme that distinction matters. A state
        # flip is reported *and* carried forward, so rawm[t..R-1] all flip, only
        # s[t] flips, and detectors t and t+1 fire: exactly the model's time-like
        # edge. A pure classical misreport would flip rawm[t] alone and fire
        # detectors t and t+2 -- a mechanism the network's error model does not
        # contain at all. A readout misreport is a different noise mechanism.
        sc.append("X_ERROR", aq, pm)
        sc.append("M", aq)  # M, never MR: no reset
        idle = [q for q in range(nq) if q not in set(aq)]
        sc.append("DEPOLARIZE1", idle, p1)
        sc.append("TICK")
    if basis == "X":
        sc.append("H", dq)
    sc.append("X_ERROR", dq, pd)
    sc.append("M", dq)

    # --- detectors, declared after every measurement so rec offsets are index - nbits
    A = lambda t, k: t * len(anc) + k  # record index of ancilla k, round t
    Dq = lambda q: R * len(anc) + q  # record index of data qubit q
    rec = lambda idx: stim.target_rec(idx - nbits)
    ai = {a: k for k, a in enumerate(anc)}
    di = {q: k for k, q in enumerate(data)}
    slots, match = detector_slots(d, R, basis)
    inv = {cell_of_anc(*a): a for a in anc}  # (slot, r, c) -> ancilla site
    for slot, t, rr, cc in slots:
        a = inv[(slot, rr, cc)]
        k = ai[a]
        if t == 0:  # s[0] vs the prepared eigenvalue
            targ = [rec(A(0, k))]
        elif t < R:  # s[t] vs s[t-1], unwrapped
            targ = [rec(A(t, k))] + ([rec(A(t - 2, k))] if t >= 2 else [])
        else:  # t == R: close against the data
            targ = [rec(Dq(di[q])) for q in anc_support(*a, d)]
            targ += [rec(A(R - 1, k))] + ([rec(A(R - 2, k))] if R >= 2 else [])
        sc.append("DETECTOR", targ)
    # logical observable: Z memory -> row i=0 (flipped by X); X memory -> col j=0
    log = [(i, 0) for i in range(d)] if basis == "X" else [(0, j) for j in range(d)]
    sc.append("OBSERVABLE_INCLUDE", [rec(Dq(di[q])) for q in log], 0)
    return sc, slots


def unwrap_extract(raw, cfg, slots=None):
    """Raw creg records -> (12-channel input volumes, logical observable).

    raw : (shots, nbits) uint8, creg order = round 0 ancillas .. round R-1, then data.
    Returns x (shots, 12, nt, nr, nc) uint8 and obs (shots,) uint8, where obs = 0
    means the logical was preserved."""
    d, R, basis = cfg["d"], cfg["rounds"], cfg["basis"]
    nr, nt, _ = lattice(d, R)
    anc, data, nbits = creg_layout(d, R)
    raw = np.asarray(raw)
    if (
        raw.ndim != 2
        or raw.shape[1] != nbits
        or len(raw) == 0
        or not np.isin(raw, (0, 1)).all()
    ):
        raise ValueError(
            "Expected nonempty binary records in the circuit's classical-bit order."
        )
    raw = raw.astype(np.uint8)
    S = raw.shape[0]
    nanc = len(anc)
    prep = prep_pattern(d, cfg["prep"])
    di = {q: k for k, q in enumerate(data)}

    am = raw[:, : nanc * R].reshape(S, R, nanc)
    dm = raw[:, nanc * R :]
    sv = am.copy()
    sv[:, 1:] ^= am[:, :-1]  # no-reset unwrap: s[t] = m[t] ^ m[t-1]

    slots_l, match = detector_slots(d, R, basis) if slots is None else (slots, None)
    if match is None:
        match = (0, 1) if basis == "X" else (2, 3)
    dets = np.zeros((S, 4, nt, nr, nr), np.uint8)
    for k, a in enumerate(anc):
        slot, r, c = cell_of_anc(*a)
        sup = [di[q] for q in anc_support(*a, d)]
        s = sv[:, :, k]
        if slot in match:
            ref = int(prep[sup].sum() % 2)  # the prepared eigenvalue
            dets[:, slot, 0, r, c] = s[:, 0] ^ ref
            dets[:, slot, R, r, c] = (dm[:, sup].sum(1) % 2).astype(np.uint8) ^ s[
                :, R - 1
            ]
        for t in range(1, R):
            dets[:, slot, t, r, c] = s[:, t] ^ s[:, t - 1]

    x = np.zeros((S, 12, nt, nr, nr), np.uint8)
    x[:, 0:8] = presence(nt, nr, nr)[None, :]
    x[:, 8:12] = dets
    log = [(i, 0) for i in range(d)] if basis == "X" else [(0, j) for j in range(d)]
    li = [di[q] for q in log]
    obs = ((dm[:, li].sum(1) + int(prep[li].sum())) % 2).astype(np.uint8)
    return x, obs


def build_circuit(cfg, nq, q_of):
    # Offline reconstruction uses Stim; Qiskit is needed only to build a circuit.
    from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister

    d, R, basis = cfg["d"], cfg["rounds"], cfg["basis"]
    anc, data, nbits = creg_layout(d, R)
    data_q = [q_of[(2 * i, 2 * j)] for (i, j) in data]
    xa_q = [q_of[a] for a in rotated_code(d)[1]]
    anc_q = [q_of[a] for a in anc]
    bits = prep_pattern(d, cfg["prep"])
    layers = cx_layers(d)

    qc = QuantumCircuit(
        QuantumRegister(nq, "q"), ClassicalRegister(nbits, "creg"), name=cfg["tag"]
    )
    for q, b in zip(data_q, bits):  # X^b then H: |0>,|1>,|+>,|->
        if b:
            qc.x(q)
    if basis == "X":
        qc.h(data_q)
    qc.barrier(data_q + anc_q)

    w = 0
    for rep in range(R):
        qc.h(xa_q)  # X ancillas into |+/->
        qc.barrier(anc_q + data_q)
        for layer in layers:
            for cs, ts in layer:
                qc.cx(q_of[cs], q_of[ts])
            qc.barrier(anc_q + data_q)
        qc.h(xa_q)  # Complete the X-check basis rotation.
        qc.barrier(anc_q + data_q)
        qc.measure(
            anc_q, list(range(w, w + len(anc_q)))
        )  # Record every round; ancillas are not reset.
        w += len(anc_q)
        qc.barrier(anc_q + data_q)

    if basis == "X":
        qc.h(data_q)  # read the data in X
    qc.measure(data_q, list(range(w, w + len(data_q))))
    w += len(data_q)
    assert w == nbits
    cfg["prep_bits"] = "".join(map(str, bits))
    cfg["nbits"] = nbits
    cfg["n_cx"] = sum(len(l) for l in layers) * R
    return qc
