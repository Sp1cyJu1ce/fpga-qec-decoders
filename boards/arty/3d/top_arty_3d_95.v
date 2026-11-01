// 95 MHz core clock derived from the Arty's 100 MHz oscillator.
// The T32 core is held in reset until the MMCM locks. The UART divisor is 825.
// This wrapper matches the physically tested T32 configuration.
`timescale 1ns/1ps
module top_arty_3d_95 #(
    parameter integer D=5,TCO_H=32,CLKS_PER_BIT=825
)(input wire CLK100MHZ, input wire btn0, input wire uart_txd_in,
  output wire uart_rxd_out, output wire [3:0] led);
    wire clk_in,clk_fb_raw,clk_fb,clk95_raw,clk95,locked;
    IBUF ibuf_inst(.I(CLK100MHZ),.O(clk_in));
    BUFG feedback_buf(.I(clk_fb_raw),.O(clk_fb));
    BUFG output_buf(.I(clk95_raw),.O(clk95));
    MMCME2_BASE #(
       .BANDWIDTH("OPTIMIZED"),.CLKIN1_PERIOD(10.0),
       .DIVCLK_DIVIDE(1),.CLKFBOUT_MULT_F(9.5),.CLKOUT0_DIVIDE_F(10.0),
       .CLKFBOUT_PHASE(0.0),.CLKOUT0_PHASE(0.0),.STARTUP_WAIT("FALSE")
    ) mmcm(.CLKIN1(clk_in),.CLKFBIN(clk_fb),.CLKFBOUT(clk_fb_raw),
       .CLKOUT0(clk95_raw),.LOCKED(locked),.PWRDWN(1'b0),.RST(1'b0));
    top_arty_3d #(.D(D),.TCO_H(TCO_H),.CLKS_PER_BIT(CLKS_PER_BIT)) core(
      .CLK100MHZ(clk95),.btn0(btn0|!locked),.uart_txd_in(uart_txd_in),
      .uart_rxd_out(uart_rxd_out),.led(led));
endmodule
