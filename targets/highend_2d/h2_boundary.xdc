# Internal endpoint boundary assumptions; no package or board timing contract.
# Separate primary clocks, with no promised phase relation.
create_clock -name core_clk -period 16.000000 [get_ports core_clk_in]
create_clock -name fabric_clk -period 10.000000 [get_ports fabric_clk_in]
set_clock_uncertainty -setup 0.050 [all_clocks]
set_clock_uncertainty -hold 0.020 [all_clocks]
set_input_delay -clock core_clk -max 0.500 [get_ports {epoch[*] frame_id[*]}]
set_input_delay -clock core_clk -min 0.100 [get_ports {epoch[*] frame_id[*]}]
set_input_delay -clock fabric_clk -max 0.500 [get_ports {rx_data[*] rx_valid tx_ready}]
set_input_delay -clock fabric_clk -min 0.100 [get_ports {rx_data[*] rx_valid tx_ready}]
set_output_delay -clock fabric_clk -max 0.500 [get_ports {tx_data[*] tx_valid rx_ready}]
set_output_delay -clock fabric_clk -min -0.100 [get_ports {tx_data[*] tx_valid rx_ready}]
set core_status [get_ports {failed error_code[*] finished accepted[*] emitted[*] compute_clocks0[*] compute_clocks1[*] compute_clocks2[*]}]
set_output_delay -clock core_clk -max 0.500 $core_status
set_output_delay -clock core_clk -min -0.100 $core_status
# Async reset is intentionally not assigned a synchronous input-delay contract.
# Post-synthesis constraints bound/classify each actual CDC path; never use blanket clock groups.
