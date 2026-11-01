// Arty A7-100T UART harness for a finite 3D volume.
// Send S, then one little-endian 16-bit word per voxel (bits 11:0 used), then G.
// The reply is A5, a four-byte little-endian core counter, tightly packed
// twelve-bit predictions in voxel order, and 5A. Serial mode is 8N1.
// LEDs show heartbeat, input loaded, busy and done.
`timescale 1ns / 1ps

module top_arty_3d #(
    parameter integer D            = 5,
    parameter integer TCO_H        = 64,

    // Shifts belong to the coefficient export; the packaged values are 15/20/19.
    parameter integer RQ_S1        = 15,
    parameter integer RQ_S2        = 20,
    parameter integer RQ_S3        = 19,
    parameter integer CLKS_PER_BIT = 868
)(
    input  wire       CLK100MHZ,
    input  wire       btn0,
    input  wire       uart_txd_in,
    output wire       uart_rxd_out,
    output wire [3:0] led
);
    localparam integer NR    = (D + 3) / 2;
    localparam integer NVOX  = (D + 1) * NR * NR;
    localparam integer NBOUT = (NVOX * 12) / 8;

    wire clk = CLK100MHZ;

    reg [1:0] rst_sync = 2'b11;
    always @(posedge clk) rst_sync <= {rst_sync[0], btn0};
    wire rst = rst_sync[1];

    wire       rx_dv;
    wire [7:0] rx_byte;
    reg        tx_dv;
    reg  [7:0] tx_byte;
    wire       tx_busy;

    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx
        (.clk(clk), .rst(rst), .rx(uart_txd_in),
         .rx_dv(rx_dv), .rx_byte(rx_byte));
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx
        (.clk(clk), .rst(rst), .tx_dv(tx_dv), .tx_byte(tx_byte),
         .tx(uart_rxd_out), .tx_busy(tx_busy));

    reg         syn_we;
    reg  [15:0] syn_addr;
    reg  [11:0] syn_word;
    reg         start;
    wire        busy, done;
    wire [31:0] lat;
    reg  [15:0] dec_raddr;
    wire [11:0] dec_q;

    decoder_top_stream_3d #(.D(D), .TCO_H(TCO_H),
                           .RQ_S1(RQ_S1), .RQ_S2(RQ_S2), .RQ_S3(RQ_S3)) u_dec (
        .clk(clk), .rst(rst),
        .syn_we(syn_we), .syn_addr(syn_addr), .syn_word(syn_word),
        .start(start), .busy(busy), .done(done), .latency_cycles(lat),
        .dec_raddr(dec_raddr), .dec_q(dec_q)
    );

    localparam [3:0] S_IDLE=4'd0, S_B0=4'd1,  S_B1=4'd2,  S_GO=4'd3,
                     S_RUN=4'd4,  S_LAT=4'd5, S_RD=4'd6,  S_RDW=4'd7,
                     S_RD2=4'd8,  S_EMIT=4'd9, S_TRL=4'd10, S_TXW=4'd11,
                     S_ARM=4'd12;
    reg [3:0]  state = S_IDLE, ret = S_IDLE;
    reg [15:0] vcnt;
    reg [7:0]  blo;
    reg [1:0]  lidx;
    reg [19:0] bitbuf;
    reg [4:0]  bitcnt;
    reg        loaded;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE; ret <= S_IDLE;
            syn_we <= 1'b0; syn_addr <= 16'd0; syn_word <= 12'd0;
            start <= 1'b0; tx_dv <= 1'b0; tx_byte <= 8'd0;
            vcnt <= 16'd0; blo <= 8'd0; lidx <= 2'd0;
            bitbuf <= 20'd0; bitcnt <= 5'd0; loaded <= 1'b0;
            dec_raddr <= 16'd0;
        end else begin
            syn_we <= 1'b0; start <= 1'b0; tx_dv <= 1'b0;

            case (state)
                S_IDLE: if (rx_dv && rx_byte == "S") begin
                    vcnt <= 16'd0; loaded <= 1'b0; state <= S_B0;
                end

                S_B0: if (rx_dv) begin blo <= rx_byte; state <= S_B1; end

                S_B1: if (rx_dv) begin
                    syn_we   <= 1'b1;
                    syn_addr <= vcnt;
                    syn_word <= {rx_byte[3:0], blo};
                    if (vcnt == NVOX - 1) begin
                        loaded <= 1'b1; state <= S_GO;
                    end else begin
                        vcnt <= vcnt + 16'd1; state <= S_B0;
                    end
                end

                S_GO: if (rx_dv) begin
                    if (rx_byte == "G") begin start <= 1'b1; state <= S_ARM; end
                    else if (rx_byte == "S") begin
                        vcnt <= 16'd0; loaded <= 1'b0; state <= S_B0;
                    end
                end

                // Wait for start acknowledgement before accepting a done level.
                S_ARM: if (busy) state <= S_RUN;

                S_RUN: if (done) begin
                    tx_byte <= 8'hA5; tx_dv <= 1'b1;
                    lidx <= 2'd0; ret <= S_LAT; state <= S_TXW;
                end

                S_LAT: begin
                    tx_byte <= lat[8*lidx +: 8]; tx_dv <= 1'b1;
                    if (lidx == 2'd3) begin
                        vcnt <= 16'd0; bitbuf <= 20'd0; bitcnt <= 5'd0;
                        ret <= S_RD;
                    end else begin
                        lidx <= lidx + 2'd1; ret <= S_LAT;
                    end
                    state <= S_TXW;
                end

                // Wait for the registered address and synchronous RAM read.
                S_RD: begin
                    dec_raddr <= vcnt; state <= S_RDW;
                end
                S_RDW: state <= S_RD2;
                S_RD2: begin
                    bitbuf <= bitbuf | ({8'd0, dec_q} << bitcnt);
                    bitcnt <= bitcnt + 5'd12;
                    vcnt   <= vcnt + 16'd1;
                    state  <= S_EMIT;
                end
                // Twelve-bit words cross byte boundaries, so carry unused bits forward.
                S_EMIT: begin
                    if (bitcnt >= 5'd8) begin
                        tx_byte <= bitbuf[7:0]; tx_dv <= 1'b1;
                        bitbuf  <= bitbuf >> 8;
                        bitcnt  <= bitcnt - 5'd8;
                        ret <= S_EMIT; state <= S_TXW;
                    end else if (vcnt < NVOX) state <= S_RD;
                    else state <= S_TRL;
                end
                S_TRL: begin
                    tx_byte <= 8'h5A; tx_dv <= 1'b1; ret <= S_IDLE; state <= S_TXW;
                end

                S_TXW: if (!tx_dv && !tx_busy) state <= ret;
                default: state <= S_IDLE;
            endcase
        end
    end

    reg [24:0] hb = 25'd0;
    always @(posedge clk) hb <= hb + 25'd1;
    assign led[0] = hb[24];
    assign led[1] = loaded;
    assign led[2] = busy;
    assign led[3] = done;

endmodule
