# Distance-five 3D check

This retained integer-hardware fixture has six time planes, each containing 4 by 4 locations with twelve binary channels. It checks a finite CNN input and does not estimate logical failure probability.

`input.hex` contains 96 twelve-bit words. Each raw-value file packs the signed 32-bit channels at one voxel per line. Layers one to three have 128 channels; layer four has twelve. Hidden activation files use eight bits per channel and the final activation file contains twelve prediction bits. Channel zero is always the least-significant field.

The testbench checks all 38,016 raw values, all 38,016 activations/predictions, producer availability and every readback word. Expected core completion is 223,603 clocks with T32 packing. It uses the core directly, without the MMCM or UART:

```text
python scripts/simulate_arty.py 3d
```
