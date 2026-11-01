# Rebuild one of the retained Arty A7-100T configurations.
# Usage: vivado -mode batch -source scripts/build_arty.tcl -tclargs 2d100 5 rtl
# Stages: rtl (elaboration only), synth, bitstream. No board is programmed.

if {$argc != 3} {
    error "Expected: <2d100|2d90|3d95> <distance> <rtl|synth|bitstream>"
}
lassign $argv preset distance stage
if {$stage ni {rtl synth bitstream}} {error "Unknown build stage: $stage"}
if {![string is integer -strict $distance]} {error "Distance must be an integer"}
set root [file dirname [file dirname [file normalize [info script]]]]
set part xc7a100tcsg324-1
switch -- $preset {
    2d100 {
        if {$distance ni {5 9 17 25 33}} {error "2d100 presets cover distances 5,9,17,25,33"}
        set dimension 2d
        set top top_arty_v2
        set cfg [list D=$distance P1=11 P2=4 P3=6 DSP_L2=1 DSP_L3=6 WROM_BLOCK=0]
        set xdc arty_a7_decoder.xdc
    }
    2d90 {
        if {$distance ni {5 33}} {error "2d90 presets cover distances 5 and 33"}
        set dimension 2d
        set top top_arty_v2_clocked
        set rom_block [expr {$distance == 5 ? 1 : 0}]
        set cfg [list D=$distance P1=11 P2=6 P3=6 DSP_L2=2 DSP_L3=4 WROM_BLOCK=$rom_block]
        set xdc arty_a7_decoder.xdc
    }
    3d95 {
        if {$distance != 5} {error "The 3d95 Arty preset is distance five"}
        set dimension 3d
        set top top_arty_3d_95
        set cfg [list D=5 TCO_H=32]
        set xdc arty_a7_100.xdc
    }
    default {error "Unknown preset: $preset"}
}

set output [file join $root build arty_${preset}_d${distance}_${stage}]
if {[file exists $output]} {error "Output already exists: $output"}
file mkdir $output
cd $output
set model_dir [file join $root models $dimension]
if {$dimension eq "3d"} {set model_dir [file join $model_dir t32]}
set coefficients [glob [file join $model_dir L*.hex]]
set expected_count [expr {$dimension eq "2d" ? 6 : 12}]
if {[llength $coefficients] != $expected_count} {error "Incomplete coefficient set"}
foreach source $coefficients {file copy $source [file tail $source]}

set sources [concat [glob [file join $root rtl arty_$dimension *.v]] \
                    [glob [file join $root boards arty $dimension *.v]]]
set_param general.maxThreads 2
set_param synth.maxThreads 1
read_verilog $sources
read_xdc [file join $root boards arty $dimension $xdc]
if {$stage eq "rtl"} {
    synth_design -rtl -top $top -part $part -generic $cfg
    puts "ARTY_ELABORATION_PASS $preset distance=$distance"
    exit 0
}

if {$dimension eq "3d"} {
    synth_design -top $top -part $part -generic $cfg \
        -directive AreaOptimized_high -control_set_opt_threshold 1
} else {
    synth_design -top $top -part $part -generic $cfg
}
if {[get_msg_config -id "Synth 8-4445" -count] > 0} {error "A coefficient ROM was not read"}
report_utilization -file utilization_synth.rpt
write_checkpoint synthesized.dcp
if {$stage eq "synth"} {exit 0}

# These directives reproduce the selected build strategy, not a timing guarantee.
if {$dimension eq "3d"} {
    opt_design -directive Explore
    place_design -directive Explore
    phys_opt_design -directive AggressiveExplore
    route_design -directive Explore
    if {[get_property SLACK [get_timing_paths -setup -max_paths 1]] < 0} {
        phys_opt_design -directive AggressiveExplore
    }
} else {
    opt_design
    place_design
    phys_opt_design
    route_design
}
write_checkpoint routed.dcp
report_utilization -file utilization_route.rpt
report_timing_summary -delay_type min_max -report_unconstrained -file timing.rpt
report_drc -file drc.rpt
report_cdc -details -file cdc.rpt
report_clocks -file clocks.rpt
set setup [get_property SLACK [get_timing_paths -setup -max_paths 1]]
set hold [get_property SLACK [get_timing_paths -hold -max_paths 1]]
if {$setup eq "" || $hold eq "" || $setup < 0 || $hold < 0} {
    error "Timing failed or was unavailable; reports retained, no bitstream written"
}
write_bitstream decoder.bit
puts "ARTY_BITSTREAM_WRITTEN $preset distance=$distance; inspect all timing/DRC reports before use"
