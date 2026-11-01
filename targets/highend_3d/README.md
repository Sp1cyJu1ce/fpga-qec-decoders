# Finite 3D cores on larger devices

These two wrappers use the same arithmetic files as `rtl/arty_3d`, with 64 hidden output-channel lanes. They retain the complete finite-volume memories and scheduler. They are single-device cores, not a distributed 3D decoder.

| Case | Device | Distance | Input voxels | Selected internal period |
|---|---|---|---|---|
| H4 | Virtex UltraScale+ VU13P, `xcvu13p-flga2577-3-e` | 5 | 96 | 10 ns (100 MHz) |
| H5 | Versal Premium VP1802, `xcvp1802-lsvc4072-3HP-e-S` | 49 | 33,800 | 12 ns (about 83.33 MHz) |

Both original routes failed their requested 4 ns (250 MHz) setup target. The retained routes were later checked at the selected periods above. That evidence covers internal computation only. External interfaces, board clocks, continuous fresh-volume throughput and physical links were not qualified.

`selected_period.xdc` retains the checked period and original boundary-delay assumptions. `requested_250mhz.xdc` preserves the historical failed target for comparison. Neither constraint file supplies physical package pins or establishes a usable board interface.

## Prepare and inspect a core

From the repository root:

```text
python scripts/retile_3d_weights.py 64 build/models_3d_t64
vivado -mode batch -source targets/highend_3d/build.tcl -tclargs h4 rtl selected
vivado -mode batch -source targets/highend_3d/build.tcl -tclargs h4 synth selected
```

Use `h5` for the larger retained case. The appropriate Vivado device support and licence must be installed. `rtl` only elaborates the hierarchy; `synth` writes a checkpoint and resource reports. The script does not place, route, qualify timing or program hardware. A new synthesis result alone cannot reproduce the thesis's routed timing result.

The retile script combines consecutive groups of 32 output channels at each kernel tap and input-channel group. It preserves every signed four-bit coefficient, along with all other model files. The T64 output was checked word for word against both retained H4/H5 coefficient sets. The script also supports T128, whose packing was checked against the thesis's T128 experiment. T128 has no separate routed hardware claim here.

For the finite-volume interface, preload all input words, pulse `start`, retain the input until `done`, and then read the predictions through the synchronous read port. The counter excludes loading, readback and quantum-controller service. See [the Arty design notes](../../docs/arty-designs.md) for the common tensor layout and arithmetic.
