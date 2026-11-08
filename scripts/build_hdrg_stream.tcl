# Run in an empty scratch directory. Supply distance, part and clock period
# with -tclargs to select a different spatial patch or FPGA target.
set distance 33
set part xc7a100tcsg324-1
set period 10.0
if {$argc > 0} {set distance [lindex $argv 0]}
if {$argc > 1} {set part [lindex $argv 1]}
if {$argc > 2} {set period [lindex $argv 2]}
if {$argc > 3 || $distance < 3 || $distance % 2 != 1 || $period <= 0} {
    error "Expected an odd distance of at least three, an FPGA part, and a positive period"
}
set root [file normalize [file join [file dirname [info script]] ..]]
read_verilog -sv [file join $root rtl hdrg_stream hdrg_stream.sv]
set xdc [open clock.xdc w]
puts $xdc "create_clock -name clk -period $period \[get_ports clk\]"
close $xdc
read_xdc clock.xdc
synth_design -mode out_of_context -top hdrg_stream -generic D=$distance -part $part
opt_design
place_design
phys_opt_design
route_design
report_route_status -file route_status.rpt
report_timing_summary -delay_type min_max -file timing.rpt
report_utilization -file utilization.rpt
# Keep the route for a second timing analysis without another implementation.
write_checkpoint -force routed.dcp
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "HDRG_WALKER_ROUTE d=$distance part=$part period_ns=$period setup_ns=$setup hold_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "Internal timing failed"}
