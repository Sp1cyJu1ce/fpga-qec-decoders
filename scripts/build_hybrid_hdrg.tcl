# In a new scratch directory: vivado -mode batch -source <path>/build_hybrid_hdrg.tcl
# Optional positional arguments are distance, part and clock period in ns.
set d 33
set part xcvu13p-flga2577-3-e
set period 10.0
if {$argc > 0} {set d [lindex $argv 0]}
if {$argc > 1} {set part [lindex $argv 1]}
if {$argc > 2} {set period [lindex $argv 2]}
if {$argc > 3 || $d < 3 || $d > 33 || $d % 2 == 0 || $period <= 0} {error "Invalid distance or clock"}
set root [file normalize [file join [file dirname [info script]] ..]]
foreach f [glob [file join $root models 2d *.hex]] {file copy -force $f .}
read_verilog [glob [file join $root rtl arty_2d *.v]]
read_verilog -sv [file join $root rtl hdrg_stream hdrg_stream.sv]
read_verilog -sv [file join $root rtl hybrid_hdrg cnn_hdrg.sv]
set xdc [open clock.xdc w]
puts $xdc "create_clock -name clk -period $period \[get_ports clk\]"
close $xdc
read_xdc clock.xdc
synth_design -mode out_of_context -top cnn_hdrg -generic D=$d -part $part
opt_design
place_design
phys_opt_design
route_design
report_route_status -file route_status.rpt
set route_file [open route_status.rpt r]
set route_status [read $route_file]
close $route_file
if {![regexp {of routable nets[^:]*:\s*(\d+)} $route_status -> routable] ||
    ![regexp {of fully routed nets[^:]*:\s*(\d+)} $route_status -> fully_routed] ||
    ![regexp {nets with routing errors[^:]*:\s*(\d+)} $route_status -> route_errors] ||
    $routable == 0 || $routable != $fully_routed || $route_errors != 0} {
    error "See route_status.rpt: incomplete route"
}
report_utilization -file utilization.rpt
report_timing_summary -delay_type min_max -file timing_requested.rpt
write_checkpoint -force routed.dcp
# A fixed routed netlist can also be checked under a looser clock constraint.
# Retain the original report, even if a slower evaluation qualifies.
set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
puts "HYBRID_REQUEST d=$d part=$part period=$period setup=$setup hold=$hold"
foreach slower {12.5 16.0 20.0 25.0 40.0} {
    if {$setup >= 0 || $slower <= $period} {continue}
    create_clock -name clk -period $slower [get_ports clk]
    set period $slower
    set setup [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
    set hold [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
}
report_timing_summary -delay_type min_max -file timing_final.rpt
puts "HYBRID_FINAL d=$d part=$part period=$period setup=$setup hold=$hold"
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {error "Internal timing failed"}
