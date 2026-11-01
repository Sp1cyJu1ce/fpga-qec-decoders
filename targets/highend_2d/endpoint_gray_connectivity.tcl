# Exact connections observed in the synthesized H2 endpoint.
# The most significant Gray bit equals the binary bit and was merged by synthesis.
# Fail closed if any actual driver, bit, primitive, clock or sync stage changes.
proc exact_endpoint_cell {name} {
 set cells [get_cells -hier -filter [format {NAME == "%s"} $name]]
 if {[llength $cells] != 1} {error "Expected one exact endpoint register: $name"}
 return $cells
}
proc exact_endpoint_pin {cell pin} {
 set pins [get_pins -of_objects $cell -filter "REF_PIN_NAME == $pin"]
 if {[llength $pins] != 1} {error "Expected one endpoint pin: $cell $pin"}
 return $pins
}
proc check_direct_endpoint_driver {dpin expected_q} {
 set nets [get_nets -segments -of_objects $dpin]
 set drivers [get_pins -of_objects $nets -filter {DIRECTION == OUT}]
 if {[llength $drivers] != 1 || [lindex $drivers 0] ne [lindex $expected_q 0]} {
  error "Unexpected endpoint connection: $dpin <- $drivers (expected $expected_q)"
 }
}
proc endpoint_gray_triplet {fifo gray binary sync1 sync2} {
 set domains [dict create ingress/wgray {fabric_clk core_clk} ingress/rgray {core_clk fabric_clk} egress/wgray {core_clk fabric_clk} egress/rgray {fabric_clk core_clk}]
 lassign [dict get $domains $fifo/$gray] source_clock destination_clock
 set source_q {}; set source_c {}; set first_d {}; set first_q {}; set second_d {}
 foreach bit {0 1 2} {
  set pointer [expr {$bit < 2 ? $gray : $binary}]
  set sc [exact_endpoint_cell [format {%s/%s_reg[%d]} $fifo $pointer $bit]]
  set fc [exact_endpoint_cell [format {%s/%s_reg[%d]} $fifo $sync1 $bit]]
  set tc [exact_endpoint_cell [format {%s/%s_reg[%d]} $fifo $sync2 $bit]]
  foreach cell [list $sc $fc $tc] {
   if {[get_property REF_NAME $cell] ne "FDCE"} {error "Unexpected pointer primitive: $cell"}
  }
  foreach cell [list $fc $tc] {
   if {[get_property ASYNC_REG $cell] ne "1"} {error "Synchronizer lost ASYNC_REG: $cell"}
   if {[get_clocks -of_objects [exact_endpoint_pin $cell C]] ne $destination_clock} {error "Unexpected sync clock: $cell"}
  }
  if {[get_clocks -of_objects [exact_endpoint_pin $sc C]] ne $source_clock} {error "Unexpected pointer-source clock: $sc"}
  set sq [exact_endpoint_pin $sc Q]; set fd [exact_endpoint_pin $fc D]
  set fq [exact_endpoint_pin $fc Q]; set td [exact_endpoint_pin $tc D]
  check_direct_endpoint_driver $fd $sq
  check_direct_endpoint_driver $td $fq
  lappend source_q $sq; lappend source_c [exact_endpoint_pin $sc C]
  lappend first_d $fd; lappend first_q $fq; lappend second_d $td
 }
 return [dict create source_q $source_q source_c $source_c first_d $first_d first_q $first_q second_d $second_d source_clock $source_clock destination_clock $destination_clock]
}
