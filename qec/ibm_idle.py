"""Final-readout memory and Bell-pair circuits, with optional XY4 pulses."""

from math import lcm
import numpy as np
from qiskit import QuantumCircuit, ClassicalRegister, QuantumRegister


class IdleExperiment:
    def __init__(self, d, data_qubits, partner_qubits=None, seed=2026, dd_cycle_us=4.0):
        data_qubits = list(data_qubits)
        partner_qubits = None if partner_qubits is None else list(partner_qubits)
        if d < 2:
            raise ValueError("Distance must be at least two.")
        if seed < 0 or not np.isfinite(dd_cycle_us) or dd_cycle_us <= 0:
            raise ValueError(
                "Seed must be nonnegative and XY4 cycle duration positive."
            )
        if len(data_qubits) != d * d + (d - 1) * (d - 1) or len(
            set(data_qubits)
        ) != len(data_qubits):
            raise ValueError(
                "Data qubits must be distinct and in logical raster order."
            )
        if partner_qubits is not None and (
            len(partner_qubits) != len(data_qubits)
            or len(set(data_qubits + partner_qubits)) != 2 * len(data_qubits)
        ):
            raise ValueError(
                "Bell partners must be distinct from one another and all data qubits."
            )
        self.data_qubits = list(data_qubits)
        self.partner_qubits = None if partner_qubits is None else list(partner_qubits)
        self.random_prep = (
            np.random.default_rng(seed)
            .integers(0, 2, len(data_qubits))
            .astype(np.uint8)
        )
        self.dd_cycle_us = dd_cycle_us

    def prep_pattern(self, prep):
        if prep in ("zeros", "plus"):
            return np.zeros(len(self.data_qubits), dtype=np.uint8)
        if prep == "ones":
            return np.ones(len(self.data_qubits), dtype=np.uint8)
        if prep == "random":
            return self.random_prep.copy()
        raise ValueError(prep)

    def delay_dt(self, t_us, backend):
        """Delay in dt units, rounded to the strictest device timing constraint."""
        tgt = backend.target
        step = lcm(
            int(getattr(tgt, "granularity", 1) or 1),
            int(getattr(tgt, "pulse_alignment", 1) or 1),
            int(getattr(tgt, "acquire_alignment", 1) or 1),
        )
        n = int(round(t_us * 1e-06 / backend.dt))
        n = max(0, int(round(n / step)) * step)
        return (n, n * backend.dt * 1000000.0)

    def x_pulse_dt(self, backend):
        """Longest available native X duration over the qubits in this circuit."""
        durations = []
        for q in self.data_qubits + (self.partner_qubits or []):
            props = backend.target["x"].get((q,))
            if props is not None and props.duration is not None:
                durations.append(int(round(props.duration / backend.dt)))
        if not durations:
            raise ValueError("XY4 requires backend X-pulse duration information.")
        return max(durations)

    def xy4_plan(self, n_dt, backend):
        """Fit k XY4 cycles into ~n_dt of wall time.
        Returns (k, half_dt, gap_dt, actual_total_dt). One cycle =
        half + X + gap + Y + gap + X + gap + Y + half, so its wall time is
        2*half + 3*gap + 4*dx."""
        tgt = backend.target
        g = lcm(
            int(getattr(tgt, "granularity", 1) or 1),
            int(getattr(tgt, "pulse_alignment", 1) or 1),
            int(getattr(tgt, "acquire_alignment", 1) or 1),
        )
        dx = int(np.ceil(self.x_pulse_dt(backend) / g)) * g
        cyc = max(1, int(round(self.dd_cycle_us * 1e-06 / backend.dt)))
        k = max(1, int(round(n_dt / cyc)))
        while k > 1 and n_dt / k < 4 * dx + 5 * g:
            k -= 1
        idle = max(0, n_dt // k - 4 * dx)
        gap = max(g, idle // 4 // g * g)
        half = max(g, gap // 2 // g * g)
        actual = k * (2 * half + 3 * gap + 4 * dx)
        return (k, half, gap, actual)

    def idle_window(self, qc, qubits, cfg, backend):
        """Insert the idle window (free or XY4) on `qubits`; returns actual dt."""
        n_dt, _ = self.delay_dt(cfg["delay_us"], backend)
        if n_dt <= 0:
            cfg["dd_cycles"] = 0
            return 0
        if not cfg["dd"]:
            for q in qubits:
                qc.delay(n_dt, q, unit="dt")
            cfg["dd_cycles"] = 0
            return n_dt
        k, half, gap, actual = self.xy4_plan(n_dt, backend)
        for _ in range(k):
            for q in qubits:
                qc.delay(half, q, unit="dt")
            for j, pulse in enumerate("XYXY"):
                if j:
                    for q in qubits:
                        qc.delay(gap, q, unit="dt")
                for q in qubits:
                    (qc.x if pulse == "X" else qc.y)(q)
            for q in qubits:
                qc.delay(half, q, unit="dt")
        cfg["dd_cycles"] = k
        return actual

    def build_single_basis_circuit(self, cfg, backend):
        bits = self.prep_pattern(cfg["prep"])
        qreg = QuantumRegister(backend.num_qubits, "q")
        creg = ClassicalRegister(len(self.data_qubits), "m")
        qc = QuantumCircuit(qreg, creg, name=cfg["tag"])
        for i, q in enumerate(self.data_qubits):
            if bits[i]:
                qc.x(q)
            if cfg["basis"] == "X":
                qc.h(q)
        qc.barrier(self.data_qubits)
        actual = self.idle_window(qc, self.data_qubits, cfg, backend)
        qc.barrier(self.data_qubits)
        if cfg["basis"] == "X":
            for q in self.data_qubits:
                qc.h(q)
        qc.measure(self.data_qubits, creg)
        cfg["prep_bits"] = "".join(map(str, bits))
        cfg["delay_us_actual"] = actual * backend.dt * 1000000.0
        return qc

    def build_bell_circuit(self, cfg, backend):
        both = [q for pair in zip(self.data_qubits, self.partner_qubits) for q in pair]
        qreg = QuantumRegister(backend.num_qubits, "q")
        mc = ClassicalRegister(len(self.data_qubits), "mc")
        mt = ClassicalRegister(len(self.data_qubits), "mt")
        qc = QuantumCircuit(qreg, mc, mt, name=cfg["tag"])
        for c, t in zip(self.data_qubits, self.partner_qubits):
            qc.h(c)
            qc.cx(c, t)
        qc.barrier(both)
        actual = self.idle_window(qc, both, cfg, backend)
        qc.barrier(both)
        for c, t in zip(self.data_qubits, self.partner_qubits):
            qc.cx(c, t)
            qc.h(c)
        for k, (c, t) in enumerate(zip(self.data_qubits, self.partner_qubits)):
            qc.measure(c, mc[k])
            qc.measure(t, mt[k])
        cfg["prep_bits"] = ""
        cfg["delay_us_actual"] = actual * backend.dt * 1000000.0
        return qc

    def build(self, config, backend):
        if config["basis"] not in ("X", "Z", "BELL") or config["delay_us"] < 0:
            raise ValueError("Invalid basis or negative delay.")
        if config["basis"] == "BELL":
            if self.partner_qubits is None:
                raise ValueError("Bell circuits require partner qubits.")
            return self.build_bell_circuit(config, backend)
        return self.build_single_basis_circuit(config, backend)
