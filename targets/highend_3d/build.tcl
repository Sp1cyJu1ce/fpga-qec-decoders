# Elaborate or synthesize the retained finite 3D high-end boundary.
# Usage: vivado -mode batch -source targets/highend_3d/build.tcl -tclargs h4 rtl selected
# Coefficients must first be generated in build/models_3d_t64.

if {$argc != 3} {error "Expected: <h4|h5> <rtl|synth> <selected|requested>"}
lassign $argv case stage clock_choice
if {$stage ni {rtl synth}} {error "Only rtl and synth stages are provided"}
if {$clock_choice ni {selected requested}} {error "Unknown clock selection"}
set target_dir [file dirname [file normalize [info script]]]
set root [file dirname [file dirname $target_dir]]
switch -- $case {
    h4 {set part xcvu13p-flga2577-3-e}
    h5 {set part xcvp1802-lsvc4072-3HP-e-S}
    default {error "Unknown retained case: $case"}
}
set model_dir [file join $root build models_3d_t64]
set coefficients [glob -nocomplain [file join $model_dir L*.hex]]
if {[llength $coefficients] != 12} {
    error "First run: python scripts/retile_3d_weights.py 64 build/models_3d_t64"
}
set output [file join $root build highend_3d_${case}_${stage}_${clock_choice}]
if {[file exists $output]} {error "Output already exists: $output"}
file mkdir $output
cd $output
foreach source $coefficients {file copy $source [file tail $source]}
set_param general.maxThreads 2
set_param synth.maxThreads 1
read_verilog [glob [file join $root rtl arty_3d *.v]]
read_verilog -sv [file join $target_dir $case highend_3d_core.sv]
set constraint [expr {$clock_choice eq "selected" ? "selected_period.xdc" : "requested_250mhz.xdc"}]
read_xdc [file join $target_dir $case $constraint]
if {$stage eq "rtl"} {
    synth_design -rtl -top highend_3d_core -part $part -mode out_of_context
    puts "HIGHEND_3D_ELABORATION_PASS $case $clock_choice"
} else {
    synth_design -top highend_3d_core -part $part -mode out_of_context -flatten_hierarchy rebuilt
    if {[get_msg_config -id "Synth 8-4445" -count] > 0} {error "Unread coefficient ROM"}
    write_checkpoint synthesized.dcp
    report_utilization -file utilization_synth.rpt
    report_ram_utilization -file ram_synth.rpt
    puts "HIGHEND_3D_SYNTHESIS_COMPLETE $case; timing is not qualified by synthesis"
}
exit 0
