# Run in a new scratch directory. All ROM paths are relative to the build.
set root [file normalize [file join [file dirname [info script]] ..]]
set period 10.0
if {$argc == 1} {set period [lindex $argv 0]}
foreach f [glob [file join $root models 2d *.hex]] {file copy -force $f .}
foreach f [glob [file join $root rtl cleanup_d3 *.hex]] {file copy -force $f .}
read_verilog [glob [file join $root rtl arty_2d *.v]]
read_verilog [file join $root rtl cleanup_d3 cleanup_d3.v]
read_verilog [file join $root rtl hybrid_d3 cnn_cleanup_d3.v]
set xdc [open hybrid_clock.xdc w]
puts $xdc "create_clock -name clk -period $period \[get_ports clk\]"
close $xdc
read_xdc hybrid_clock.xdc
synth_design -mode out_of_context -top cnn_cleanup_d3 -part xc7a100tcsg324-1
opt_design
place_design
phys_opt_design
route_design
report_route_status -file route_status.rpt
report_timing_summary -delay_type min_max -file timing.rpt
report_utilization -file utilization.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "HYBRID_RESULT period_ns=$period setup_slack_ns=$setup hold_slack_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "Internal timing failed at $period ns"}
