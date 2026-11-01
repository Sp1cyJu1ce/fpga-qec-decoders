# ============================================================================
# arty_a7_100.xdc -- constraints for top_arty_3d on the Digilent Arty A7-100T
# ============================================================================

# 100 MHz system clock
set_property -dict {PACKAGE_PIN E3 IOSTANDARD LVCMOS33} [get_ports CLK100MHZ]
create_clock -period 10.000 -name sys_clk_pin -waveform {0.000 5.000} [get_ports CLK100MHZ]

# btn0 = active-high reset
set_property -dict {PACKAGE_PIN D9 IOSTANDARD LVCMOS33} [get_ports btn0]

# USB-UART bridge (FTDI): uart_txd_in is the host's TX (our RX)
set_property -dict {PACKAGE_PIN A9  IOSTANDARD LVCMOS33} [get_ports uart_txd_in]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports uart_rxd_out]

# LEDs
set_property -dict {PACKAGE_PIN H5  IOSTANDARD LVCMOS33} [get_ports {led[0]}]
set_property -dict {PACKAGE_PIN J5  IOSTANDARD LVCMOS33} [get_ports {led[1]}]
set_property -dict {PACKAGE_PIN T9  IOSTANDARD LVCMOS33} [get_ports {led[2]}]
set_property -dict {PACKAGE_PIN T10 IOSTANDARD LVCMOS33} [get_ports {led[3]}]

# UART RX and the reset button are asynchronous inputs with two-flop synchronizers.
set_false_path -from [get_ports uart_txd_in]
set_false_path -from [get_ports btn0]
