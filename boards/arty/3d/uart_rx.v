// 8N1 receiver with a two-flop asynchronous-input synchronizer.
// rx_dv pulses for one clock. The stop bit is timed, not checked for framing errors.
`timescale 1ns / 1ps

module uart_rx #(
    parameter integer CLKS_PER_BIT = 868
)(
    input  wire       clk,
    input  wire       rst,
    input  wire       rx,
    output reg        rx_dv,
    output reg [7:0]  rx_byte
);
    localparam [2:0] S_IDLE = 3'd0, S_START = 3'd1, S_DATA = 3'd2,
                     S_STOP = 3'd3, S_CLEAN = 3'd4;

    reg [1:0]  sync = 2'b11;
    always @(posedge clk) sync <= {sync[0], rx};
    wire rxs = sync[1];

    reg [2:0]  state = S_IDLE;
    reg [15:0] cnt = 16'd0;
    reg [2:0]  bidx = 3'd0;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;  rx_dv <= 1'b0;  cnt <= 16'd0;  bidx <= 3'd0;
        end else begin
            rx_dv <= 1'b0;
            case (state)
                S_IDLE: begin
                    cnt <= 16'd0;  bidx <= 3'd0;
                    if (rxs == 1'b0) state <= S_START;
                end
                S_START: begin
                    if (cnt == (CLKS_PER_BIT - 1) / 2) begin
                        if (rxs == 1'b0) begin cnt <= 16'd0; state <= S_DATA; end
                        else state <= S_IDLE;
                    end else cnt <= cnt + 16'd1;
                end
                S_DATA: begin
                    if (cnt == CLKS_PER_BIT - 1) begin
                        cnt <= 16'd0;
                        rx_byte[bidx] <= rxs;
                        if (bidx == 3'd7) state <= S_STOP;
                        else bidx <= bidx + 3'd1;
                    end else cnt <= cnt + 16'd1;
                end
                S_STOP: begin
                    if (cnt == CLKS_PER_BIT - 1) begin
                        cnt <= 16'd0;  rx_dv <= 1'b1;  state <= S_CLEAN;
                    end else cnt <= cnt + 16'd1;
                end
                S_CLEAN: state <= S_IDLE;
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
