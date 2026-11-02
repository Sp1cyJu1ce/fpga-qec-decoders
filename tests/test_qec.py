"""Offline numerical and circuit checks; no IBM service or board is opened."""

from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
import importlib.util
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest
from unittest.mock import patch
import numpy as np
import pymatching
from qec.spatial import Geometry, FFTReference
from qec.hdrg import SurfaceHDRG
from qec.decode_ibm import hdrg_for, idle_errors
from qec.memory import Geometry3D, memory_circuit
from qec.simulate_2d import correlated_model, check_correlated
from qec.simulate_3d import circuit_for
from qec import ibm_memory
from qec.decay import decay, fit_decay
from qec.io import protocol

ROOT = Path(__file__).resolve().parents[1]
HAS_TORCH = importlib.util.find_spec("torch") is not None
HAS_QISKIT = importlib.util.find_spec("qiskit") is not None


def unpack_hex(path, channels, bits, signed=False):
    rows = []
    for line in path.read_text().split():
        word = int(line, 16)
        row = [
            (word >> (bits * channel)) & ((1 << bits) - 1)
            for channel in range(channels)
        ]
        if signed:
            row = [
                value - (1 << bits) if value >= 1 << (bits - 1) else value
                for value in row
            ]
        rows.append(row)
    return np.asarray(rows, np.int64)


class NumericalTests(unittest.TestCase):
    def test_spatial_integer_matches_hdl_fixture(self):
        folder = ROOT / "examples/arty_2d_d5"
        image = unpack_hex(folder / "input.hex", 4, 1).reshape(19, 19, 4)[5:-5, 5:-5]
        inputs = image.transpose(2, 0, 1)[None]
        logits, layers = FFTReference("integer", 9).forward(
            inputs, check=True, return_layers=True
        )
        for index, width in enumerate((11, 21)):
            expected = (
                unpack_hex(folder / f"layer{index+1}_activation.hex", 9, width)
                .reshape(9, 9, 9)
                .transpose(2, 0, 1)
            )
            np.testing.assert_array_equal(layers[index][0], expected)
        expected = (
            unpack_hex(folder / "layer3_raw.hex", 2, 36, True)
            .reshape(9, 9, 2)
            .transpose(2, 0, 1)
        )
        np.testing.assert_array_equal(logits[0], expected)
        decisions = (
            unpack_hex(folder / "decisions.hex", 2, 1)
            .reshape(9, 9, 2)
            .transpose(2, 0, 1)
        )
        np.testing.assert_array_equal(logits[0] > 0, decisions)

    @unittest.skipUnless(HAS_TORCH, "Install requirements-cnn.txt for 3D inference.")
    def test_memory_integer_matches_hdl_fixture(self):
        from qec.networks import IntegerCNN, configure, arithmetic_contract

        configure("cpu", 2)
        folder = ROOT / "examples/arty_3d_d5"
        inputs = (
            unpack_hex(folder / "input.hex", 12, 1)
            .reshape(6, 4, 4, 12)
            .transpose(3, 0, 1, 2)[None]
        )
        layers = IntegerCNN().forward(inputs, return_layers=True)
        for index in range(3):
            expected = (
                unpack_hex(folder / f"layer{index+1}_activation.hex", 128, 8)
                .reshape(6, 4, 4, 128)
                .transpose(3, 0, 1, 2)
            )
            np.testing.assert_array_equal(layers[index][0].numpy(), expected)
        expected = (
            unpack_hex(folder / "layer4_raw.hex", 12, 32, True)
            .reshape(6, 4, 4, 12)
            .transpose(3, 0, 1, 2)
        )
        np.testing.assert_array_equal(layers[3][0].numpy(), expected)
        self.assertTrue(arithmetic_contract()["signed_32bit_output_safe"])

    def test_hdrg_paths_clear_checks(self):
        rng = np.random.default_rng(42)
        for distance in (3, 4, 5):
            geometry = Geometry(distance)
            errors = rng.integers(0, 2, (24, 2, geometry.nd), dtype=np.uint8)
            syndromes = geometry.syndromes(errors)
            corrections = hdrg_for(distance).decode_batch(syndromes)
            self.assertFalse(geometry.outcomes(errors ^ corrections)["nonclear"].any())
            if distance % 2:
                np.testing.assert_array_equal(
                    corrections, SurfaceHDRG(distance).decode_batch(syndromes)
                )

    def test_correlated_graph_explains_every_single_pauli(self):
        geometry = Geometry(3)
        matcher = pymatching.Matching.from_detector_error_model(
            correlated_model(geometry, 0.1), enable_correlations=True
        )
        self.assertEqual(
            check_correlated(geometry, 0.1, matcher)["single_pauli_cases"], 39
        )

    def test_hdrg_rejects_nonbinary_values_before_conversion(self):
        decoder = SurfaceHDRG(3).decoders[0]
        for value in (-1, 0.5, 2, 256, np.nan):
            syndrome = np.zeros(len(decoder.coords))
            syndrome[0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                decoder.decode(syndrome)

    def test_effective_decay_fit(self):
        time = np.linspace(0, 100, 15)
        probability = decay(time, 0.02, 0.4, 35, 1)
        result = fit_decay(time, probability, False)
        self.assertAlmostEqual(result["time_us"], 35, places=4)
        self.assertTrue(result["fit_uncertainty_available"])
        self.assertLess(result["time_us_standard_error"], 0.001)
        self.assertFalse(result["lifetime_beyond_observed_range"])


class CircuitTests(unittest.TestCase):
    @unittest.skipUnless(HAS_QISKIT, "Install requirements-ibm.txt for Qiskit checks.")
    def test_invalid_online_delay_is_rejected_before_account_access(self):
        from qec import ibm_experiments

        args = [
            "ibm_experiments",
            "idle",
            "--delay-us",
            "nan",
            "--submit",
            "--output",
            "build/invalid-delay-check",
        ]
        with patch("sys.argv", args), patch.object(
            ibm_experiments, "runtime_service"
        ) as account, redirect_stderr(StringIO()):
            with self.assertRaises(SystemExit) as error:
                ibm_experiments.main()
            self.assertEqual(error.exception.code, 2)
            account.assert_not_called()

    def test_reset_circuits_have_no_noiseless_failures(self):
        for basis in ("X", "Z"):
            circuit, slots = memory_circuit(3, 3, basis, 0)
            self.assertFalse(
                circuit.compile_detector_sampler(seed=12)
                .sample(32, append_observables=True)
                .any()
            )
            full, _ = circuit_for(
                dict(d=3, rounds=3, basis=basis, pprime=0.01, noise="full")
            )
            reduced, removed = circuit_for(
                dict(
                    d=3,
                    rounds=3,
                    basis=basis,
                    pprime=0.01,
                    noise="remove_four_data_idles",
                )
            )
            self.assertEqual(removed, 12)
            self.assertEqual(full.num_detectors, reduced.num_detectors)
            self.assertEqual(len(slots), full.num_detectors)

    def test_no_reset_conversion_matches_stim(self):
        for basis in ("X", "Z"):
            for prep in ("zeros", "ones", "random"):
                ibm_memory.PREP_BITS.clear()
                ibm_memory.SEED = 2026
                config = dict(d=3, rounds=3, basis=basis, prep=prep)
                circuit, slots = ibm_memory.stim_circuit(
                    config, p1=0.002, p2=0.01, pm=0.02, pd=0.01
                )
                raw = circuit.compile_sampler(seed=9).sample(32)
                volumes, observable = ibm_memory.unwrap_extract(raw, config)
                detectors, expected = circuit.compile_m2d_converter().convert(
                    measurements=raw, separate_observables=True
                )
                geometry = Geometry3D(3, 3, basis)
                np.testing.assert_array_equal(
                    geometry.full_detectors(volumes[:, 8:]), detectors
                )
                np.testing.assert_array_equal(observable, expected[:, 0])

    @unittest.skipUnless(HAS_QISKIT, "Install requirements-ibm.txt for Qiskit checks.")
    def test_qiskit_memory_schedule_has_no_midcircuit_reset(self):
        from qec.ibm_experiments import preview_backend
        from qec.ibm_device import select_memory_patch

        backend = preview_backend()
        mapping = select_memory_patch(backend, 3)
        ibm_memory.PREP_BITS.clear()
        config = dict(d=3, rounds=3, basis="Z", prep="zeros", tag="test")
        circuit = ibm_memory.build_circuit(config, backend.num_qubits, mapping)
        self.assertEqual(circuit.count_ops().get("reset", 0), 0)
        self.assertEqual(circuit.count_ops()["cx"], config["n_cx"])
        self.assertEqual(circuit.count_ops()["measure"], config["nbits"])

    @unittest.skipUnless(HAS_QISKIT, "Install requirements-ibm.txt for Qiskit checks.")
    def test_idle_xy4_and_bell_readout(self):
        from qec.ibm_experiments import preview_backend
        from qec.ibm_idle import IdleExperiment

        backend = preview_backend()
        experiment = IdleExperiment(2, range(5), range(5, 10))
        for basis in ("X", "Z", "BELL"):
            config = dict(
                basis=basis,
                prep="bell" if basis == "BELL" else "zeros",
                delay_us=8,
                dd=True,
                tag="test",
            )
            circuit = experiment.build(config, backend)
            self.assertGreater(config["dd_cycles"], 0)
            self.assertGreater(config["delay_us_actual"], 0)
            self.assertEqual(circuit.num_clbits, 10 if basis == "BELL" else 5)

    @unittest.skipUnless(HAS_QISKIT, "Install requirements-ibm.txt for Qiskit checks.")
    def test_classical_bit_order(self):
        from qiskit.primitives.containers import BitArray
        from qec.ibm_experiments import unpack

        bits = BitArray.from_samples(["100", "001"])
        np.testing.assert_array_equal(unpack(bits), [[0, 0, 1], [1, 0, 0]])

    def test_idle_input_validation(self):
        with self.assertRaises(ValueError):
            idle_errors(
                dict(d=3, basis="Z", prep_bits="0" * 13), {"m": np.full((2, 13), 2)}
            )
        with self.assertRaises(ValueError):
            idle_errors(
                dict(d=3, basis="Z", prep_bits="0"), {"m": np.zeros((2, 13), np.uint8)}
            )


class OptionalDependencyTests(unittest.TestCase):
    def test_offline_imports_do_not_require_torch_or_qiskit(self):
        # A fresh interpreter catches accidental imports even when both are installed.
        script = textwrap.dedent("""
            import importlib.abc
            import sys
            class BlockOptional(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in ('torch', 'qiskit'):
                        raise ModuleNotFoundError(fullname, name=fullname)
            sys.meta_path.insert(0, BlockOptional())
            from qec import decay, decode_ibm, ibm_memory, simulate_2d, simulate_3d
            circuit, _ = simulate_3d.circuit_for(dict(
                d=3, rounds=3, basis='Z', pprime=0.005, noise='full'))
            assert circuit.num_detectors > 0
            try:
                from qec import networks
            except ModuleNotFoundError as error:
                assert 'requirements-cnn.txt' in str(error)
            else:
                raise AssertionError('3D inference must report the missing dependency')
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_protocol_records_absent_optional_package(self):
        from importlib.metadata import PackageNotFoundError, version

        def without_torch(name):
            if name == "torch":
                raise PackageNotFoundError(name)
            return version(name)

        with TemporaryDirectory() as folder, patch(
            "qec.io.importlib.metadata.version", side_effect=without_torch
        ):
            record = protocol(folder, {"experiment": "cpu-2d"})
            self.assertIsNone(record["software"]["torch"])
            self.assertEqual(protocol(folder, record["settings"]), record)
            with self.assertRaises(ValueError):
                protocol(folder, {"experiment": "different"})


if __name__ == "__main__":
    unittest.main()
