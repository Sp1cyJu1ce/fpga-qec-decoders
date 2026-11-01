# High-end FPGA components

These are the two wrappers used for the distance-33 component studies. They
instantiate the same arithmetic and control modules as the small examples.
The wrapper clock buffers come from AMD's device libraries.

| Case | Component | Device | Qualified internal clocks |
| --- | --- | --- | --- |
| H6 | Complete resident worker, 65 × 65 map, three spatial lanes | `xcvp1902-vsvb6865-3HP-e-S` | 100 MHz |
| H2 | One interior worker of a 4 × 4 partition, with ingress and egress queues | `xcvu13p-flga2577-3-e` | Worker: 62.5 MHz; fabric: 100 MHz |

H2 is worker 5. It owns a 16 × 16 region of the 65 × 65 map. Its local memory is
allocated for 27 × 27 sites, while this interior worker needs a 26 × 26 region
including the five-site halo. Its output allocation is 17 × 17, allowing for the
largest partition. The wrapper does not instantiate the other 15 workers or a
switch.

The supplied XDC files describe synchronous component boundaries, not package
pins or measured board links. There are no transceivers, host drivers, external
memory controllers or quantum-control interfaces here. These designs require
the relevant Vivado device support and licence; they are not Arty targets.

## Loading one component in Vivado

Use one source list at a time. Each list is relative to the repository root.
The resident and distributed implementations have some identical module names
and must not be loaded together.

For H6, start Vivado in Tcl mode at the repository root:

```tcl
set root [pwd]
set manifest [open targets/highend_2d/h6_sources.f r]
foreach source_file [split [string trim [read $manifest]] "\n"] {
    read_verilog -sv [file join $root $source_file]
}
close $manifest
read_xdc [file join $root targets/highend_2d/h6_core.xdc]
file mkdir [file join $root build h6]
cd [file join $root build h6]
synth_design -top highend_worker_core -part xcvp1902-vsvb6865-3HP-e-S -mode out_of_context -flatten_hierarchy rebuilt
```

For H2, start a separate Vivado session at the repository root:

```tcl
set root [pwd]
set manifest [open targets/highend_2d/h2_sources.f r]
foreach source_file [split [string trim [read $manifest]] "\n"] {
    read_verilog -sv [file join $root $source_file]
}
close $manifest
read_xdc [file join $root targets/highend_2d/h2_boundary.xdc]
file mkdir [file join $root build h2]
cd [file join $root build h2]
synth_design -top highend_endpoint -part xcvu13p-flga2577-3-e -mode out_of_context -flatten_hierarchy rebuilt
source [file join $root targets/highend_2d/endpoint_cdc_policy.tcl]
```

These commands load and synthesise the components. They do not perform placement,
routing, timing qualification or board programming. Synthesis is substantial;
the small Icarus examples are the quicker way to check functionality.

The H2 post-synthesis policy checks the actual reset destinations and each
Gray-pointer synchroniser connection before adding timing constraints. It is
specific to the mapping used in the study, including the shared most significant
binary/Gray pointer bit. If synthesis changes register names or connectivity,
the policy stops with an error. Review the new mapping rather than removing
those checks. The script writes two small diagnostic files in the build folder.

## Interpreting the clocks

The thesis first routed H6 with a 250 MHz request and H2 with a 100 MHz worker
request. Both missed those requested internal setup times. The existing routes
were then analysed at the slower clocks shown above; they were not rerouted for
those clocks. The supplied XDCs contain the accepted slower-clock assumptions.
A fresh implementation using them can produce a different route and must be
checked again.

The H6 functional result was 2,180 cycles for each of three unpaused, preloaded
patterns, equivalent to 21.8 microseconds at 100 MHz. Input loading and external
readback were outside that interval. H2 establishes the internal feasibility of
one endpoint, not the timing of an entire multi-board machine. The full scope is
summarised in [the results](../../docs/results.md).
