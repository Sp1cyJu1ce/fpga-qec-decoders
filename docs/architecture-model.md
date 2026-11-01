# FPGA architecture model

This model explores how the CNN workload could be divided across larger FPGAs.
It estimates the arithmetic, memory ports and neighbour transfers needed by each
tile, then schedules those operations. I used it to compare board counts and to
identify where communication can outweigh the benefit of smaller tiles.

These are conditional estimates. The selected allocations are not complete,
routed FPGA designs, and the model does not establish a board clock or a physical
link latency. The RTL and on-board tests are separate from this study.

## Running a case

Only Python's standard library is needed. Run these commands from the repository
root. The default is one small 2D case:

```sh
python scripts/explore_architecture.py
python scripts/explore_architecture.py --model 2D --distances 17 25 33 --boards 1 4 16 64 --output results/2d_architecture.json
python scripts/explore_architecture.py --model 3D --distances 33 --boards 64 --stream-rounds 512 --arrival-us 1.1 --output results/3d_architecture.json
```

`--full-grid` uses the thesis distance grid and 1, 4, 16 and 64 boards for the
chosen device. It is optional. Each completed case is written to the output file,
so the completed results remain available if a longer sweep is interrupted.

The available device profiles are VU9P, VU13P, VU19P, VP1802 and VP1902. Their
capacity and package assumptions are recorded in `DEVICE_DATA` in
`qec/architecture.py`, using the AMD DS890 and DS950 device overviews. Package
choice matters because the transceiver count is not interchangeable across all
packages of a device. UltraRAM counts are listed for context; the present memory
allocation uses BRAM and does not treat unused UltraRAM as a free replacement.

## What is modelled

The 2D CNN has three layers with 11 by 11 spatial kernels. Its input map has side
length `2*d - 1`. The 3D CNN has four layers with 3 by 3 by 3 kernels, spatial side
length `(d + 3) // 2`, and `d + 1` time planes for a window with `d` measurement
rounds. The network widths and arithmetic widths are explicit in `NETS`.

A square board mesh divides the spatial map into rectangular tiles. Edge tiles
can differ by one row or column. Each layer exchanges the surrounding values
needed by its convolution. Transfers follow horizontal then vertical mesh paths,
including longer paths when a kernel reaches beyond an adjacent tile. Repeated
requests for the same value are charged separately.

The search considers separate layer engines and a shared arithmetic pool, with
either replicated RAM windows or raster line buffers. For 2D it also considers
fixed-weight layer kernels, serial complete frames and direct output writes.
The two main parallelism parameters are `V`, the number of spatial input lanes,
and `P`, the number of convolution terms processed per output channel per cycle.
When a layer has more terms than `P`, it takes several cycles to complete them.

The search uses a shortlist for each service-time threshold. Its answer is the
best allocation among the candidates it keeps, not a proof of a global optimum.
It can report that no enumerated allocation fits without proving that the device
could never implement the network.

## Assumptions that affect the result

The defaults are 250 MHz, 400 Gbit/s in each directed cardinal link and 50 ns per
hop. The transport model allows 90% payload efficiency and adds a 128-bit header
to each logical source-to-destination request. It also counts source RAM reads
and destination RAM writes, so a fast link cannot bypass the memory-port limit.
The transceiver screen counts 25 Gbit/s lanes. It does not verify board routing,
protocol logic, error recovery or transceiver placement.

By default, every physical link receives the same bandwidth as boards are added.
To test a fixed total mesh bandwidth, use:

```sh
python scripts/explore_architecture.py --distances 33 --boards 4 16 64 --mesh-budget-gbps 3200 --output results/fixed_mesh.json
```

That budget is divided between `4*p*(p-1)` directed links for a `p` by `p` mesh,
subject to the per-link limit. A single-board case has no mesh traffic.

The nominal resource allowance is 60% of LUTs, 60% of flip-flops, 75% of DSPs and
70% of BRAM. The conservative allowance is 45%, 45%, 60% and 55%, respectively.
The permissive allowance is 75%, 75%, 85% and 85%. These fractions reserve room
for implementation overhead; they are not placement guarantees.

Arithmetic area costs use Artix-7 out-of-context synthesis anchors plus explicit
width allowances. The fixed-weight 2D layer measurements are in
`models/architecture/hardwired2d_resources.csv`. They are separate layer synthesis
results, not routed decoder results. In particular, the second layer exceeds the
anchor Artix device's LUT capacity. Applying these counts to a larger device is
an area estimate and does not transfer a timing result between device families.

## Reading the output

Each JSON case includes two independently selected allocations:

- `winners.finite` minimises `full_pass_us`: time to finish the complete frame or
  finite time window after its input is available to the model.
- `winners.service` minimises `steady_ii_us`: the service interval for successive
  frames in 2D, or successive time planes in 3D. A shorter interval means higher
  throughput. It is not the time taken by one complete 3D window.

The winners can use different allocations. Their latency, service interval and
resource counts must therefore be read together, rather than combining the best
numbers from both into one design. All resource counts are per FPGA, using the
largest tile. The JSON also includes the number of screened candidates and the
resources that rejected them.

The `io` entries add ideal packed input and output transfers at 32, 128 and
400 Gbit/s. They exclude protocol, controller and residual-decoder work. They do
not establish end-to-end quantum feedback latency.

For a 3D case, `--stream-rounds` enables a temporal occupancy check. A map plane
remains live until its consumer has finished; simultaneous allocation and release
are counted conservatively. The skip path retains at least 64 planes, while map
capacity follows the finite window. A passing check establishes bounded storage
only over the tested event-model horizon. A failing cadence can accumulate data
in abstract queues that a real finite buffer would not hold. This is not an RTL
FIFO or backpressure test.

## Checks

```sh
python -m unittest discover -s tests -p test_architecture_model.py
```

The tests cover partition and halo accounting, invalid configurations, and the
accepted 2D and 3D results at small distances and at distance 33. They check the
portable model against recorded study values; they do not turn model estimates
into measured FPGA timings.
