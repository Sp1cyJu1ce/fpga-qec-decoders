# 2D integer coefficients

These six files hold the fixed coefficients used by the 2D RTL. The model follows Gicev, Hollenberg and Usman, [*A scalable and fast artificial neural network syndrome decoder for surface codes*](https://doi.org/10.22331/q-2023-07-12-1058). They are existing trained coefficients, not a new model trained for this repository.

Each weight file has 121 rows in kernel-row, kernel-column order. Within a row, signed eight-bit weights are packed with input channel varying fastest, then output channel. The first coefficient occupies the least-significant byte. Channel counts are 4→9, 9→9 and 9→2. Bias files contain one sign-extended 48-bit value per output channel.

The files retain their tested coefficient values, with line endings normalized for Git. RTL uses 16/24/36-bit raw sums and 11/21-bit hidden activations. Vivado may warn when loading the 48-bit bias files into narrower accumulators. Only redundant sign-extension bits are removed for these coefficients. Replacing the weights requires new range checks and numerical tests.

The coefficient provenance is separate from the hardware implementation. No blanket software licence in this repository grants rights to redistribute these inherited model files.
