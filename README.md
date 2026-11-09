# FPGA neural decoders for quantum error correction

Verilog, SystemVerilog and Python code from my Master of Computer Science thesis at the University of Melbourne: **Resource-Constrained FPGA Implementations of Surface-Code Neural Decoders: Verification, Experimental Evaluation and Scalable Integration**.

The project asks how a neural decoder can fit on an FPGA, how much parallel computation helps, and what changes when the calculation is divided across several devices. It includes the 2D and 3D designs tested on an Arty A7-100T, together with larger resident and distributed designs used in simulation.

<p align="center">
  <img src="docs/assets/arty-board.jpg" alt="Arty A7-100T connected by USB during an earlier distance-five demonstration" width="760">
</p>

My Arty A7-100T test setup. The [short demonstration clip](docs/assets/arty-board-demo.mp4) shows an early distance-five run.

## Start here

- [Arty designs and build instructions](docs/arty-designs.md): memory-based 2D and 3D cores, board interfaces and small simulation examples.
- [Resident and distributed designs](docs/scalable-designs.md): overlapping layers, tile ownership and simulated communication.
- [Global cleanup after the CNN](docs/scalable-cleanup.md) follows a prediction through to a complete correction. It includes the implementation diagram from Appendix B.8 and both sets of 32 distance-33 timing results.
- [Results and their scope](docs/results.md): board measurements, simulated completion times and internal timing checks on larger devices.
- [Quantum experiments and decoder comparisons](docs/quantum-experiments.md): IBM circuits, saved-data decoding, CNN/HDRG/MWPM simulations, pruning and decay fits.
- [Architecture estimates](docs/architecture-model.md): resource and communication models for larger FPGA systems.
- [Model and source attribution](ATTRIBUTION.md): the research behind the networks and the included coefficient files.

The small examples exercise the packaged HDL in simulation and stage their inputs under `build/`. The Arty and scalable-core examples use Python launchers. The distance-three cleanup check also uses the software decoder dependencies listed in its guide.

With Vivado's simulator on `PATH`:

```sh
python scripts/simulate_arty.py 2d
python scripts/simulate_arty.py 3d
```

With Icarus Verilog's `iverilog` and `vvp` on `PATH`:

```sh
python scripts/simulate_arty.py 2d --simulator iverilog
python scripts/simulate_scalable.py resident
```

The 2D Arty check does not require Vivado. The 3D Arty check uses Vivado's simulator.

With NumPy installed, `python scripts/simulate_distances.py` checks fresh deterministic inputs at distances 5, 9, 17 and 33 using Icarus and an independent integer reference. It also checks the expected core cycle counts. The [example guide](examples/arty_2d_distances/README.md) explains the configurations.

The distributed example uses Verilator, CMake and a C++20 compiler:

```sh
python scripts/simulate_scalable.py distributed
```

Each command compares the RTL with stored integer-reference results and reports a pass or failure. The scalable examples may take several minutes. The design guides explain tool-path options, input formats and additional checks. Building a board bitstream additionally requires Vivado with Artix-7 device support.

## What is implemented?

| Design | Main idea | Verification in the thesis |
|---|---|---|
| [Arty 2D](rtl/arty_2d/) | Share convolution arithmetic and read several memory banks in parallel | Physical Arty tests through code distance 33 |
| [Arty 3D](rtl/arty_3d/) | Include measurement history and start a layer when its inputs are ready | Physical distance-five tests, plus larger finite-volume simulations |
| [Resident 2D](rtl/resident_2d/) | Keep data on the device and overlap successive convolution layers | RTL simulations through distance 97; selected distance-33 internal timing on VP1902 |
| [Distributed 2D](rtl/distributed_2d/) | Assign each output to one worker and exchange the neighbouring values it needs | Simulated networks through distance 65 and up to 64 workers |
| [Global cleanup](docs/scalable-cleanup.md) | Read CNN decisions, form the residual and return both correction masks | Connected CNN and HDRG in RTL at ten distances through 33, with separate exhaustive small-patch checks |

The four neural designs produce local predictions. The [connected 2D design](rtl/hybrid_hdrg/cnn_hdrg.sv) reads those predictions and sends the remaining checks to two HDRG engines. The [small-patch experiments](docs/distance-three-cleanup.md) also test a lookup and an exact minimum-weight search. A physical feedback experiment would also need measurement transport and a controller that applies or records that correction.

```mermaid
flowchart LR
    A[Measured checks] --> B[Input encoding]
    B --> C[FPGA neural core]
    C --> D[Local predictions]
    D --> E[Residual and global decoding]
    E --> F[Correction or Pauli-frame update]
```

The Python package implements input encoding, residual construction and global decoding. I used it to compare decoders on simulated errors and on records collected from IBM hardware. The FPGA feedback path was tested in RTL simulation.

For a small software comparison:

```sh
python -m pip install -r requirements-core.txt
python -m qec.simulate_2d --distances 3 5 --shots 128 --output build/spatial
python -m unittest discover -s tests -p test_qec.py -v
```

For 3D neural inference, install `requirements-cnn.txt`. IBM circuit construction and submission use `requirements-ibm.txt`. Install `requirements-analysis.txt` if you need both. The IBM circuit commands build locally unless `--submit` is explicitly supplied. Credentials and measurement files stay outside the published source.

## Repository layout

```text
rtl/          Convolution engines, memories and worker control
boards/       Arty top-level modules and pin/clock constraints
models/       Floating-point and integer model parameters
qec/          Geometry, software decoders, IBM circuits and analysis
examples/     Small inputs, expected outputs and testbenches
scripts/      Portable simulation and build entry points
tests/        Offline numerical, circuit and board-protocol checks
targets/      Selected larger-device components and constraints
results/      Selected thesis results in CSV form
docs/         Design notes, results and figures
```

The resident and distributed designs reuse some module names for different interfaces. Use the launchers to select the source files for each build. The large files under `rtl/hardwired_2d/` are generated fixed-coefficient arithmetic, shared by the scalable designs.

## Reading the results

More parallel workers help when there is enough communication capacity to feed them. With a fixed total bandwidth, extra workers can exchange so much overlapping data that completion becomes slower. Memory layout, arithmetic, clock timing and communication therefore have to be considered together.

At distance 33, HDRG found a correction faster after the CNN reduced the number of fired checks. The CNN took longer to run than the search time it saved. The [per-frame results](results/independent_cleanup_d33_frames.csv) give the original search time, the residual search time and the time for the complete loaded frame.

## Attribution and citation

The neural models build on work by Spiro Gicev, Lloyd C. L. Hollenberg and Muhammad Usman. See [ATTRIBUTION.md](ATTRIBUTION.md) for the papers and a description of the parameter exports. Please cite the original model papers as well as this implementation where relevant. GitHub's citation menu uses [CITATION.cff](CITATION.cff).

**Author:** Mohit Garg, University of Melbourne.

**Supervisors:** Dr Lloyd C. L. Hollenberg, Dr Rajkumar Buyya and Dr Spiro Gicev.
