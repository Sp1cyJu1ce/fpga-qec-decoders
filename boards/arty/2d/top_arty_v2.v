// Arty A7-100T UART harness for the 2D core (8N1, nominal 115200 baud).
// Send S, then packed input nibbles (low nibble first), then G.
// The reply is A5, a four-byte little-endian core counter, packed two-bit
// predictions (lowest pixel first), and 5A. Unused final-byte bits are zero.
// LEDs show heartbeat, input loaded, busy and done. btn0 resets the harness.
`timescale 1ns / 1ps

module top_arty_v2 #(
    parameter integer D            = 5,
    parameter integer P1           = 11,
    parameter integer P2           = 4,
    parameter integer P3           = 4,
    parameter integer DSP_L2       = 2,
    parameter integer DSP_L3       = 4,
    parameter integer WROM_BLOCK   = 0,
    parameter integer CLKS_PER_BIT = 868,

    // Keep the coefficient files in the build working directory.
    parameter WFILE1 = "L0_weights_par.hex", parameter BFILE1 = "L0_bias.hex",
    parameter WFILE2 = "L1_weights_par.hex", parameter BFILE2 = "L1_bias.hex",
    parameter WFILE3 = "L2_weights_par.hex", parameter BFILE3 = "L2_bias.hex"
)(
    input  wire       CLK100MHZ,
    input  wire       btn0,
    input  wire       uart_txd_in,
    output wire       uart_rxd_out,
    output wire [3:0] led
);
    localparam integer OW        = 2*D - 1;
    localparam integer NWIN      = OW*OW;
    localparam integer FRAME_W   = OW + 10;
    localparam integer NPIX_F    = FRAME_W*FRAME_W;
    localparam integer NBYTES_IN = (NPIX_F + 1)/2;

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
    reg  [3:0]  syn_nib;
    reg         start;
    wire        busy, done;
    wire [31:0] lat;
    reg  [15:0] dec_raddr;
    wire [1:0]  dec_q;

    decoder_top_v2 #(
        .D(D), .P1(P1), .P2(P2), .P3(P3),
        .DSP_L2(DSP_L2), .DSP_L3(DSP_L3), .WROM_BLOCK(WROM_BLOCK),
        .WFILE1(WFILE1), .BFILE1(BFILE1),
        .WFILE2(WFILE2), .BFILE2(BFILE2),
        .WFILE3(WFILE3), .BFILE3(BFILE3)
    ) u_dec (
        .clk(clk), .rst(rst),
        .syn_we(syn_we), .syn_addr(syn_addr), .syn_nib(syn_nib),
        .start(start), .busy(busy), .done(done), .latency_cycles(lat),
        .dec_raddr(dec_raddr), .dec_q(dec_q)
    );

    localparam [3:0] S_IDLE=4'd0,  S_LO=4'd1,   S_HI=4'd2,   S_GO=4'd3,
                     S_RUN=4'd4,   S_HDR=4'd5,  S_LAT=4'd6,  S_RD=4'd7,
                     S_RD2=4'd8,   S_DEC=4'd9,  S_TRL=4'd10, S_TXW=4'd11,
                     S_RDW=4'd12,  S_RUNW=4'd13;
    reg [3:0]  state = S_IDLE, ret = S_IDLE;
    reg [15:0] bcnt;
    reg [7:0]  cur;
    reg [1:0]  lidx;
    reg [15:0] px;
    reg [1:0]  sub;
    reg [7:0]  obyte;
    reg        loaded;

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE; ret <= S_IDLE;
            syn_we <= 1'b0; syn_addr <= 16'd0; syn_nib <= 4'd0;
            start <= 1'b0; tx_dv <= 1'b0; tx_byte <= 8'd0;
            bcnt <= 16'd0; cur <= 8'd0; lidx <= 2'd0;
            px <= 16'd0; sub <= 2'd0; obyte <= 8'd0; loaded <= 1'b0;
            dec_raddr <= 16'd0;
        end else begin
            syn_we <= 1'b0; start <= 1'b0; tx_dv <= 1'b0;

            case (state)
                S_IDLE: if (rx_dv && rx_byte == "S") begin
                    bcnt <= 16'd0; loaded <= 1'b0; state <= S_LO;
                end
                S_LO: if (rx_dv) begin
                    cur      <= rx_byte;
                    syn_we   <= 1'b1;
                    syn_addr <= {bcnt[14:0], 1'b0};
                    syn_nib  <= rx_byte[3:0];
                    state    <= S_HI;
                end
                S_HI: begin
                    if ({bcnt[14:0], 1'b1} < NPIX_F) begin
                        syn_we   <= 1'b1;
                        syn_addr <= {bcnt[14:0], 1'b1};
                        syn_nib  <= cur[7:4];
                    end
                    if (bcnt == NBYTES_IN-1) begin
                        loaded <= 1'b1; state <= S_GO;
                    end else begin
                        bcnt <= bcnt + 16'd1; state <= S_LO;
                    end
                end
                S_GO: if (rx_dv) begin
                    if (rx_byte == "G") begin start <= 1'b1; state <= S_RUNW; end
                    else if (rx_byte == "S") begin
                        bcnt <= 16'd0; loaded <= 1'b0; state <= S_LO;
                    end
                end
                // Wait for the previous done level to clear after start.
                S_RUNW: state <= S_RUN;
                S_RUN: if (done) begin
                    tx_byte <= 8'hA5; tx_dv <= 1'b1;
                    lidx <= 2'd0; ret <= S_LAT; state <= S_TXW;
                end
                S_LAT: begin
                    tx_byte <= lat[8*lidx +: 8]; tx_dv <= 1'b1;
                    if (lidx == 2'd3) begin
                        px <= 16'd0; sub <= 2'd0; obyte <= 8'd0; ret <= S_RD;
                    end else begin
                        lidx <= lidx + 2'd1; ret <= S_LAT;
                    end
                    state <= S_TXW;
                end
                // Account for the registered address and synchronous result RAM.
                S_RD:  begin dec_raddr <= px; state <= S_RDW; end
                S_RDW: state <= S_RD2;
                S_RD2: begin
                    if (px < NWIN)
                        obyte[{sub,1'b0} +: 2] <= dec_q;
                    if (sub == 2'd3 || px == NWIN-1) state <= S_DEC;
                    else begin sub <= sub + 2'd1; px <= px + 16'd1; state <= S_RD; end
                end
                S_DEC: begin
                    tx_byte <= obyte; tx_dv <= 1'b1; state <= S_TXW;
                    if (px == NWIN-1) ret <= S_TRL;
                    else begin
                        px <= px + 16'd1; sub <= 2'd0; ret <= S_RD;
                    end
                end
                S_TRL: begin
                    tx_byte <= 8'h5A; tx_dv <= 1'b1; ret <= S_IDLE; state <= S_TXW;
                end
                S_TXW: begin
                    if (ret == S_RD) obyte <= 8'd0;
                    if (!tx_dv && !tx_busy) state <= ret;
                end
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
