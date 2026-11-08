# Recheck a saved route at another period without changing placement or wiring.
# Example: vivado -mode batch -source <script> -tclargs routed.dcp 11.112
if {$argc != 2} {error "Expected a routed checkpoint and clock period in ns"}
set checkpoint [lindex $argv 0]
set period [lindex $argv 1]
if {$period <= 0} {error "The period must be positive"}
open_checkpoint $checkpoint
create_clock -name clk -period $period [get_ports clk]
report_timing_summary -delay_type min_max -file rechecked_timing.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "HDRG_WALKER_RECHECK period_ns=$period setup_ns=$setup hold_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "Internal timing failed"}
