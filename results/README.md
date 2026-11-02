# Result tables

These CSVs contain selected thesis results. They describe different timing boundaries, so read the scope before comparing times. Blank fields mean that a value was not established. None of the distributed results is a measurement between physical FPGA boards.

| File | What it records |
|---|---|
| [board_results.csv](board_results.csv) | Physical Arty core counters and the configured clock. Loading, UART readback and global decoding are excluded. |
| [distributed_scaling.csv](distributed_scaling.csv) | Complete finite frames under independent 32 Gbit/s ports or a fixed 32 Gbit/s aggregate budget, through distance 65. |
| [distributed_128gbps.csv](distributed_128gbps.csv) | The distance-33 comparison with 128 Gbit/s aggregate capacity in each direction. |
| [distributed_queue_depth.csv](distributed_queue_depth.csv) | The distance-nine pilot with four, eight or sixteen records per queue. |
| [distributed_clock_matched.csv](distributed_clock_matched.csv) | Twelve distance-33 frames at a 16 ns worker period and 10 ns fabric period. |
| [resident_cycles.csv](resident_cycles.csv) | Preloaded single-worker cycles for serial and overlapped layers, through distance 97. |
| [native_timing.csv](native_timing.csv) | Internal timing and resources for selected routed large-device components. |
| [decoder_quality_2d.csv](decoder_quality_2d.csv) | One-pass CNN plus global decoding versus the same global decoder alone, at eight spatial distances. |
| [decoder_quality_3d.csv](decoder_quality_3d.csv) | The corresponding finite-memory comparison at four distances, with X and Z memories kept separate. |
| [pruning_complete_decoding.csv](pruning_complete_decoding.csv) | Saved complete-decoding outcomes after pruning floating-point weights, at distances 9, 17 and 33. |

Distributed completion runs from the first accepted host input to the final returned prediction. It includes simulated loading, exchange, queues and output collection, but excludes global decoding and quantum-controller feedback. The main bandwidth comparisons use 10 ns worker and fabric periods and four-record queues. Budgets apply separately to each direction. The four-worker 128 Gbit/s point reuses the identical independent-port execution; it is not an additional experiment. There is no separate one-worker 128 Gbit/s point because that worker's port remains capped at 32 Gbit/s.

In the queue-depth pilot both clocks have a 10 ns period and each worker has a 32 Gbit/s port in each direction. Each worker has two queues. Eight records per queue therefore use 5,120 bits in total, 2,560 bits more than the four-record configuration. Increasing depth from eight to sixteen did not improve completion in these selected cases. This pilot does not establish the best queue depth for every workload.

The clock-matched table uses the clocks checked for one interior VU13P worker, applied to the full sixteen-worker simulation. `normal` and `reset_recovery` each contain the same six test patterns. The interrupted partial frame is excluded. Timing includes remaining memory clearing after input acceptance and excludes asserted-reset time. The small spread across patterns and reset alignment is not a statistical latency distribution. Only one worker geometry has the associated internal device timing evidence.

Resident cycles run from accepted start to the last result write with inputs already loaded. `unpaused_cycles` is the completion count without intentional read pauses. `frames_validated` includes the paused fixtures where used; it does not mean that every frame had that cycle count. The table reports cycles without assuming that every configuration achieves the same FPGA clock. Only the distance-33, three-lane point has the separate VP1902 100 MHz result described in [the results guide](../docs/results.md).

Frames and repeated board transactions are hardware test inputs, not independent samples for estimating quantum decoding failure. The software studies use separate random error trials and IBM measurements, as described in [the experiment guide](../docs/quantum-experiments.md). The small runnable examples reproduce checks of the packaged implementations rather than the full campaign behind these tables.

## Decoder comparisons

The 2D table uses independent depolarising data errors with probability 0.13 and perfect checks on an unrotated patch. Failure includes a remaining syndrome or a logical error in either Pauli component. The 3D table uses the full simulated circuit-noise model with parameter 0.005, as many rounds as the distance, and a rotated patch. Its outcome is incorrect final logical parity for the prepared X or Z memory. It cannot be read as simultaneous failure of both memories. `mwpm_dem` uses circuit-derived edge weights; `correlated_dem` also uses correlations between faults. Both tables apply one neural pass before the named global decoder.

All comparisons share trials between the methods. `worse_only` counts trials where the hybrid fails and the baseline succeeds; `better_only` counts the reverse. Their difference divided by `shots` is the change in failure rate. Negative values favour the hybrid. Differences and interval endpoints ending in `_pp` are percentage points, while error probabilities and sparsities are fractions.

`score_lower_pp` and `score_upper_pp` are pointwise 95% Tango asymptotic paired-score intervals, computed from the saved discordant counts. They are not exact or simultaneous confidence bounds. The [paired-score helper](../qec/paired_score.py) reproduces them without running a simulation. Its method follows Tango (1998), discussed by [Fagerland, Lydersen and Laake (2014)](https://doi.org/10.1002/sim.6148). The original simulation commands retain their conservative paired-interval output fields; those fields have not been silently redefined.

The pruning table uses one float64 neural pass on an unpunctured spatial patch at error probability 0.13. It sets small weights to zero while retaining biases, then runs MWPM or HDRG. The same random errors are used across sparsity levels. Each row compares the pruned hybrid with both the unpruned hybrid and global decoding alone. Counts and paired-score endpoints are preserved for both comparisons. These are saved software experiment results, not timings or resource savings from a sparse FPGA design. The existing `qec.pruning` command studies local residual pixels around defects and does not reproduce this separate complete-decoding experiment.
