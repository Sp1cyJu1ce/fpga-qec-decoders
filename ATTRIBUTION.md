# Attribution

## Neural models

The network architectures and trained parameters originate from research by Spiro Gicev, Lloyd C. L. Hollenberg and Muhammad Usman:

- **2D:** S. Gicev, L. C. L. Hollenberg and M. Usman, “A scalable and fast artificial neural network syndrome decoder for surface codes,” *Quantum* 7, 1058 (2023). [Published paper](https://doi.org/10.22331/q-2023-07-12-1058).
- **3D:** S. Gicev, L. C. L. Hollenberg and M. Usman, “Fully convolutional 3D neural network decoders for surface codes with syndrome circuit noise.” The implementation was developed from the [arXiv v1 model](https://arxiv.org/abs/2506.16113v1); the later [published article](https://doi.org/10.1088/2058-9565/ae5fc9) is a separate version.

The `models/` directories contain the floating-point parameters and the integer exports used by the FPGA implementations. The NPZ files hold numerical arrays only, without training objects or notebook metadata. They are exports of the existing trained models, not independently trained networks. The generated `rtl/hardwired_2d/` modules embed the same 2D integer coefficients in arithmetic expressions. The implementation and thesis explain the chosen integer formats and their limitations.

## Implementation

The FPGA implementation work, experiments, documentation and figures form part of Mohit Garg's Master of Computer Science research at the University of Melbourne. The contribution includes memory organisation, scheduling, integer hardware, verification and scaling studies. The Python package includes the experiment circuits, numerical references and decoder comparisons. HDRG follows the local clustering approach discussed in the 2D paper, with the path construction and tie-breaking specified in the code. It does not claim authorship of the original neural models.

OpenAI ChatGPT assisted with Verilog debugging and refactoring, and with figure refinement, as acknowledged in the thesis.

## Media and tools

The Arty photograph and short video are taken from the project's earlier distance-five demonstration. The clip is shortened and silent; the photograph is a frame from it. They illustrate the physical setup and do not establish the timing of later implementations. The figures are from the thesis.

AMD Vivado and Icarus Verilog are external tools. Their executables, proprietary primitive models, device databases and licence files are not distributed here. The Arty board is a Digilent product.

## Licensing

No project-wide open-source licence has been applied. In particular, this repository does not grant a new licence over the inherited trained parameters. Source availability and authorship attribution do not replace permission to redistribute or relicense those materials. Please contact the author before redistributing the model exports or a modified package.
