# Run in a scratch directory. The timing check covers register-to-register paths.
set root [file normalize [file join [file dirname [info script]] ..]]
read_verilog [file join $root rtl minimum_weight_d3 minimum_weight_d3.v]
set constraints [open clock.xdc w]
puts $constraints {create_clock -name clk -period 10.000 [get_ports clk]}
close $constraints
read_xdc clock.xdc
synth_design -mode out_of_context -top minimum_weight_d3 -part xc7a100tcsg324-1
opt_design
place_design
route_design
report_route_status -file route_status.rpt
report_timing_summary -delay_type min_max -file timing.rpt
report_utilization -file utilization.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "MINIMUM_WEIGHT_RESULT clock_ns=10.000 setup_slack_ns=$setup hold_slack_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {
    error "The core missed the 100 MHz internal timing constraint."
}
