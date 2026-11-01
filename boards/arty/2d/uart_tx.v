// 8N1 transmitter. Present tx_byte and pulse tx_dv only when tx_busy is low.
// Bytes are sent least-significant bit first.
`timescale 1ns / 1ps

module uart_tx #(
    parameter integer CLKS_PER_BIT = 868
)(
    input  wire       clk,
    input  wire       rst,
    input  wire       tx_dv,
    input  wire [7:0] tx_byte,
    output reg        tx,
    output reg        tx_busy
);
    localparam [1:0] S_IDLE=2'd0, S_START=2'd1, S_DATA=2'd2, S_STOP=2'd3;

    reg [1:0]  state = S_IDLE;
    reg [15:0] cnt   = 16'd0;
    reg [2:0]  bidx  = 3'd0;
    reg [7:0]  sh    = 8'd0;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE; tx <= 1'b1; tx_busy <= 1'b0; cnt <= 0; bidx <= 0;
        end else begin
            case (state)
                S_IDLE: begin
                    tx <= 1'b1; tx_busy <= 1'b0; cnt <= 0; bidx <= 0;
                    if (tx_dv) begin sh <= tx_byte; tx_busy <= 1'b1;
                                     state <= S_START; end
                end
                S_START: begin
                    tx <= 1'b0;
                    if (cnt == CLKS_PER_BIT-1) begin cnt <= 0; state <= S_DATA; end
                    else cnt <= cnt + 1'b1;
                end
                S_DATA: begin
                    tx <= sh[bidx];
                    if (cnt == CLKS_PER_BIT-1) begin
                        cnt <= 0;
                        if (bidx == 3'd7) state <= S_STOP;
                        else bidx <= bidx + 1'b1;
                    end else cnt <= cnt + 1'b1;
                end
                S_STOP: begin
                    tx <= 1'b1;
                    if (cnt == CLKS_PER_BIT-1) begin
                        cnt <= 0; state <= S_IDLE; tx_busy <= 1'b0;
                    end else cnt <= cnt + 1'b1;
                end
            endcase
        end
    end
endmodule
