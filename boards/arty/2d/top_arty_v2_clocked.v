// 90 MHz core clock derived from the Arty's 100 MHz oscillator.
// The decoder remains reset until the MMCM locks. The UART divisor is 781.
// Select 11/6/6 taps and 2/4 DSP lanes through the supplied build preset.
`timescale 1ns/1ps
module top_arty_v2_clocked #(
 parameter integer D=5,P1=11,P2=4,P3=6,DSP_L2=1,DSP_L3=6,WROM_BLOCK=0
)(input wire CLK100MHZ,input wire btn0,input wire uart_txd_in,
  output wire uart_rxd_out,output wire[3:0]led);
 wire ci,fb_raw,fb,co_raw,co,locked;
 IBUF ib(.I(CLK100MHZ),.O(ci));
 BUFG bf(.I(fb_raw),.O(fb));BUFG bo(.I(co_raw),.O(co));
 MMCME2_BASE #(.BANDWIDTH("OPTIMIZED"),.CLKIN1_PERIOD(10.0),
 .DIVCLK_DIVIDE(1),.CLKFBOUT_MULT_F(9.0),.CLKOUT0_DIVIDE_F(10.0),
 .CLKFBOUT_PHASE(0.0),.CLKOUT0_PHASE(0.0),.STARTUP_WAIT("FALSE"))
 mmcm(.CLKIN1(ci),.CLKFBIN(fb),.CLKFBOUT(fb_raw),.CLKOUT0(co_raw),
 .LOCKED(locked),.PWRDWN(1'b0),.RST(1'b0));
 top_arty_v2 #(.D(D),.P1(P1),.P2(P2),.P3(P3),.DSP_L2(DSP_L2),
 .DSP_L3(DSP_L3),.WROM_BLOCK(WROM_BLOCK),.CLKS_PER_BIT(781)) core(
 .CLK100MHZ(co),.btn0(btn0|!locked),.uart_txd_in(uart_txd_in),
 .uart_rxd_out(uart_rxd_out),.led(led));
endmodule
