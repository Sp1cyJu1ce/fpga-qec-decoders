# Run in a scratch directory. The optional argument is distance 3 or 5.
set distance 3
if {$argc == 1} {set distance [lindex $argv 0]}
if {$argc > 1 || $distance ni {3 5}} {error "Expected distance 3 or 5"}
set root [file normalize [file join [file dirname [info script]] ..]]
read_verilog -sv [file join $root rtl hdrg_online hdrg_online.sv]
set xdc [open hdrg_clock.xdc w]
puts $xdc {create_clock -name clk -period 10.000 [get_ports clk]}
close $xdc
read_xdc hdrg_clock.xdc
synth_design -mode out_of_context -top hdrg_online -generic D=$distance -part xc7a100tcsg324-1
opt_design
place_design
route_design
report_route_status -file route_status.rpt
report_timing_summary -delay_type min_max -file timing.rpt
report_utilization -file utilization.rpt
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "HDRG_RESULT d=$distance period_ns=10.000 setup_slack_ns=$setup hold_slack_ns=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {
    error "The routed core missed the internal timing constraint."
}
