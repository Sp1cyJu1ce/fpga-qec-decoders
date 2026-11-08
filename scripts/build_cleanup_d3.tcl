# Run from a scratch directory with Vivado in batch mode. The design is
# registered at both boundaries so the routed paths describe the core itself.
set project_root [file normalize [file join [file dirname [info script]] ..]]
set design_dir [file join $project_root rtl cleanup_d3]
file copy -force [file join $design_dir x_corrections.hex] .
file copy -force [file join $design_dir z_corrections.hex] .
read_verilog [file join $design_dir cleanup_d3.v]
set constraints [open cleanup_clock.xdc w]
puts $constraints {create_clock -name clk -period 10.000 [get_ports clk]}
close $constraints
read_xdc cleanup_clock.xdc
synth_design -mode out_of_context -top cleanup_d3 -part xc7a100tcsg324-1
opt_design
place_design
route_design
report_route_status -file route_status.rpt
report_timing_summary -delay_type min_max -file timing.rpt
report_utilization -file utilization.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "CLEANUP_RESULT period_ns=10.000 setup_slack_ns=$setup hold_slack_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {
    error "The core did not meet the 100 MHz internal timing constraint."
}
