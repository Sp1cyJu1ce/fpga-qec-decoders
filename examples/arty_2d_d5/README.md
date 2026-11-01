# Distance-five 2D check

This is one retained sparse binary fixture from the thesis's integer-hardware checks. The 9 by 9 four-channel map has a five-cell zero halo, giving 361 loaded words. It is a numerical stress input, not a new quantum-device measurement or a decoder-quality sample.

`input.hex` packs channel zero in the least-significant bit. `layer1_raw.hex`, `layer2_raw.hex` and `layer3_raw.hex` pack signed channel sums using 16, 24 and 36 bits per channel. The two activation files use 11 and 21 bits per channel. `decisions.hex` has two bits per map location. Channel zero is always the least-significant field.

The testbench checks all 1,620 raw values, all 1,620 activations/predictions, and every readback word. Expected core completion is 4,377 clocks with 11/4/6 taps. Run it from the repository root:

```text
python scripts/simulate_arty.py 2d
```
