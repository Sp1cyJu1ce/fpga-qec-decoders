# 3D integer coefficients

The `t32` folder contains the tested integer export for the network described by Gicev, Hollenberg and Usman, [*Fully convolutional 3D neural network decoders for surface codes with syndrome circuit noise*](https://arxiv.org/abs/2506.16113v1). The thesis uses this version's model and circuit description. These are existing learned coefficients, not a newly trained model.

The packed files are for 32 output channels per group in layers two and three. Layers one and four retain groups of eight and twelve output channels respectively. Main weight widths are eight, four, four and eight bits. Skip weights are signed sixteen-bit values.

Weight rows are ordered by output-channel group, kernel tap and input-channel group. Within each row the input lane varies fastest, followed by the output lane; the first coefficient occupies the least-significant bits. Kernel taps are ordered by time, row and column. Layer one reads all twelve binary input channels together. Later main paths read eight channels per cycle. `L4_skip.hex` has one row per kernel tap and packs twelve input channels for each of twelve outputs.

`L*_B.hex` files contain signed 32-bit biases. `L*_M.hex` contain unsigned sixteen-bit requantisation multipliers. The hidden-layer shifts are 15, 20 and 19. Requantisation applies ReLU, adds the half-step rounding term, shifts, then clamps to 255. The last layer uses a strict positive-sign test.

The files retain their tested coefficient values, with line endings normalized for Git. Changing the group width requires repacking the weights, not simply changing `TCO_H`. The Arty preset sets `TCO_H=32`. For wider middle layers, see [the high-end targets](../../targets/highend_3d/README.md).

The coefficient provenance is separate from the hardware implementation. No blanket software licence in this repository grants rights to redistribute these inherited model files.
