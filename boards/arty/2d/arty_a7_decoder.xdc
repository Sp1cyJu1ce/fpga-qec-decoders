# arty_a7_decoder.xdc -- Arty A7-100T pins used by the 2D UART harness.
# Pin sites taken from Digilent's Arty A7 master XDC; if your board revision
# differs, cross-check against the master file that ships with the board
# (https://github.com/Digilent/digilent-xdc), but these are the standard
# Rev E sites.

## 100 MHz system clock
set_property -dict { PACKAGE_PIN E3  IOSTANDARD LVCMOS33 } [get_ports CLK100MHZ]
create_clock -add -name sys_clk -period 10.000 -waveform {0 5} [get_ports CLK100MHZ]

## USB-UART bridge (FTDI): txd_in is host->FPGA, rxd_out is FPGA->host
set_property -dict { PACKAGE_PIN A9  IOSTANDARD LVCMOS33 } [get_ports uart_txd_in]
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports uart_rxd_out]

## Buttons: btn0 = synchronous reset
set_property -dict { PACKAGE_PIN D9  IOSTANDARD LVCMOS33 } [get_ports btn0]

## Green LEDs ld0..ld3
set_property -dict { PACKAGE_PIN H5  IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
set_property -dict { PACKAGE_PIN J5  IOSTANDARD LVCMOS33 } [get_ports {led[1]}]
set_property -dict { PACKAGE_PIN T9  IOSTANDARD LVCMOS33 } [get_ports {led[2]}]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports {led[3]}]

## The UART receiver synchronizes its input. Serial I/O is not a synchronous
## external interface, so these two pin paths are excluded from core timing.
set_false_path -from [get_ports uart_txd_in]
set_false_path -to   [get_ports uart_rxd_out]
