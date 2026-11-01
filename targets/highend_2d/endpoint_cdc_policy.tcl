# Post-synthesis constraints for the specific H2 endpoint mapping.
# Check reset destinations and Gray-pointer paths before applying exceptions.
source [file join [file dirname [info script]] endpoint_gray_connectivity.tcl]
set reset_pins [get_pins -hier -filter {REF_PIN_NAME == CLR || REF_PIN_NAME == PRE}]
if {[llength $reset_pins] == 0} {error "No asynchronous reset pins found"}
set reset_ends [all_fanout -flat -endpoints_only -trace_arcs all [get_ports reset]]
if {[llength $reset_ends] != 56} {error "Unexpected raw-reset endpoint count"}
set ra [open reset_endpoints.txt w]
foreach pin $reset_ends {
 puts $ra "$pin [get_property REF_PIN_NAME $pin]"
 if {[get_property REF_PIN_NAME $pin] ni {CLR PRE}} {error "Raw reset reaches non-async endpoint: $pin"}
}
close $ra
set_false_path -from [get_ports reset] -to $reset_pins
set_max_delay -datapath_only 10.000 -from [get_clocks core_clk] -to [get_clocks fabric_clk]
set_max_delay -datapath_only 10.000 -from [get_clocks fabric_clk] -to [get_clocks core_clk]
set audit [open cdc_queries.txt w]
set crossings 0
foreach fifo {ingress egress} {
 foreach {gray binary sync1 sync2} {wgray wbin wgray_r1 wgray_r2 rgray rbin rgray_w1 rgray_w2} {
  set pins [endpoint_gray_triplet $fifo $gray $binary $sync1 $sync2]
  set starts [dict get $pins source_c]; set ends [dict get $pins first_d]
  if {[llength $starts] != 3 || [llength $ends] != 3} {error "Incomplete Gray triplet"}
  set_bus_skew 10.000 -from $starts -to $ends
  puts $audit "$fifo $gray $sync1 : $pins"
  incr crossings 3
 }
}
close $audit
if {$crossings != 12} {error "Expected exactly 12 Gray-pointer crossings"}
