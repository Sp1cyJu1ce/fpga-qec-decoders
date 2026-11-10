# Checks across 2D distances

Run from the repository root with NumPy and Icarus Verilog installed:

```text
python scripts/simulate_distances.py
```

The default run checks three successive frames at each of distances 5, 9, 17 and 33. It generates reproducible sparse and dense syndrome inputs, evaluates the fixed integer model with NumPy, and compares every two-bit prediction read from the RTL. There is no reset between frames at a given distance. Results, generated inputs and logs go into a new directory under `build`. Use `--distances 5 9` for a shorter run, or `--tool-dir PATH` when Icarus is not on `PATH`. In one Linux run with Icarus 12.0, the four simulations took about 13 minutes in total, including about nine minutes at distance 33. The default time limit is 30 minutes per compile or simulation.

| Distance | Lanes in layers 1 / 2 / 3 | Expected core clocks |
|---|---|---|
| 5 | 11 / 4 / 6 | 4,377 |
| 9 | 11 / 4 / 6 | 12,561 |
| 17 | 11 / 4 / 6 | 41,601 |
| 33 | 11 / 6 / 6 | 103,894 |

All cases use `DSP_L2=1` and `DSP_L3=6`. At distance 33, these directives differ from the `(11,6,6)` Arty bitstream preset, although the integer operations are the same. The run checks the banked core's predictions, completion counts and reuse between frames. It does not establish routed timing, board fit or logical error rates. The generated syndrome bits are hardware test inputs, not samples from a quantum noise model. The [distance-five fixture](../arty_2d_d5) separately checks every layer's raw sum and activation.
