// Three-layer 2D integer CNN with independent layer sequencers.
// Load the padded input frame before pulsing start. Hidden layers wait for
// the preceding layer to commit every pixel their next convolution needs.
// The counter covers core computation, excluding input loading and readback.
//
// The build script selects the tested 11/4/6 or 11/6/6 tap allocation. Module
// defaults are retained from the verified source; they are not a board preset.
`timescale 1ns / 1ps

module decoder_top_v2 #(
    parameter integer D        = 5,
    parameter integer P1       = 11,
    parameter integer P2       = 4,
    parameter integer P3       = 4,
    parameter integer KSIZE    = 11,
    parameter integer PAD      = 5,
    parameter integer W_W      = 8,

    // Widths apply to the supplied integer export; recheck them for new weights.
    parameter integer A1_W     = 11,
    parameter integer A2_W     = 21,
    parameter integer ACC1_W   = 16,
    parameter integer ACC2_W   = 24,
    parameter integer ACC3_W   = 36,

    // Each layer-2 DSP lane uses 81 multipliers; each layer-3 lane uses 18.
    parameter integer DSP_L2   = 2,
    parameter integer DSP_L3   = 4,
    parameter integer WROM_BLOCK = 0,

    parameter integer MARGIN   = 0,

    parameter WFILE1 = "L0_weights_par.hex", parameter BFILE1 = "L0_bias.hex",
    parameter WFILE2 = "L1_weights_par.hex", parameter BFILE2 = "L1_bias.hex",
    parameter WFILE3 = "L2_weights_par.hex", parameter BFILE3 = "L2_bias.hex"
)(
    input  wire        clk,
    input  wire        rst,

    input  wire        syn_we,
    input  wire [15:0] syn_addr,
    input  wire [3:0]  syn_nib,

    input  wire        start,
    output reg         busy,
    output reg         done,
    output reg  [31:0] latency_cycles,

    input  wire [15:0] dec_raddr,
    output reg  [1:0]  dec_q
);
    function integer clog2; input integer v; integer i; begin
        clog2 = 0; for (i = v-1; i > 0; i = i >> 1) clog2 = clog2 + 1; end
    endfunction

    localparam integer OW      = 2*D - 1;
    localparam integer NWIN    = OW*OW;
    localparam integer FRAME_W = OW + 2*PAD;
    localparam integer NPIX_F  = FRAME_W*FRAME_W;
    localparam integer CH      = 9;
    localparam integer G1      = (KSIZE + P1 - 1)/P1;
    localparam integer G2      = (KSIZE + P2 - 1)/P2;
    localparam integer G3      = (KSIZE + P3 - 1)/P3;
    localparam integer AW_F    = clog2(NPIX_F);
    localparam integer AW_M    = clog2(NWIN);
    localparam integer FWA     = CH*A1_W;
    localparam integer FWB     = CH*A2_W;
    localparam integer LEAD    = PAD*OW + PAD;

    wire        s1_we, s2_we, s3_we;
    wire [15:0] s1_px, s2_px, s3_px;
    wire [FWA-1:0] s1_data;
    wire [FWB-1:0] s2_data;
    wire [1:0]  s3_sign;

    // Counts are exclusive: wr1=1 means pixel zero has been committed.
    reg [15:0] wr1, wr2;
    always @(posedge clk) begin
        if (rst || start) begin wr1 <= 16'd0; wr2 <= 16'd0; end
        else begin
            if (s1_we) wr1 <= wr1 + 16'd1;
            if (s2_we) wr2 <= wr2 + 16'd1;
        end
    end

    wire [AW_F-1:0] s1_base;
    wire [P1*4-1:0] s1_q;
    wire            s1_run, s1_first, s1_last, s1_active;
    wire [7:0]      s1_wbase;
    wire [P1-1:0]   s1_lv;
    wire [15:0]     s1_pxi, s1_pxlead;

    stage_seq #(.KSIZE(KSIZE), .P(P1), .PAD(PAD), .OW(OW), .OH(OW),
                .NWIN(NWIN), .SRC_PADDED(1), .STRIDE(FRAME_W),
                .AW(AW_F), .G(G1), .LEAD(LEAD+MARGIN)) u_seq1 (
        .clk(clk), .rst(rst), .start(start), .gate(1'b1),
        .run(s1_run), .first(s1_first), .last(s1_last), .px(s1_pxi),
        .wbase(s1_wbase), .lane_v(s1_lv), .base(s1_base), .pxlead(s1_pxlead), .active(s1_active)
    );

    mem_rep #(.W(4), .N(NPIX_F), .P(P1), .AW(AW_F)) u_syn (
        .clk(clk), .we(syn_we), .waddr(syn_addr[AW_F-1:0]), .wdata(syn_nib),
        .rbase(s1_base), .rq(s1_q)
    );

    conv_engine_v2 #(.CIN(4), .COUT(CH), .KSIZE(KSIZE), .P(P1),
                     .IN_W(1), .W_W(W_W), .ACC_W(ACC1_W), .OUT_W(A1_W),
                     .SRC_BINARY(1), .DO_RELU(1), .DSP_LANES(0), .WROM_BLOCK(WROM_BLOCK),
                     .WFILE(WFILE1), .BFILE(BFILE1)) u_l1 (
        .clk(clk), .rst(rst), .run(s1_run), .first(s1_first), .last(s1_last),
        .px(s1_pxi), .wbase(s1_wbase), .lane_v(s1_lv), .src_q(s1_q),
        .res_we(s1_we), .res_px(s1_px), .res_data(s1_data), .res_sign()
    );

    wire [AW_M-1:0]  s2_base;
    wire [P2*FWA-1:0] s2_q;
    wire             s2_run, s2_first, s2_last, s2_active;
    wire [7:0]       s2_wbase;
    wire [P2-1:0]    s2_lv;
    wire [15:0]      s2_pxi, s2_pxlead;

    // Wait for the conservative look-ahead address. Once the whole map is
    // written, the consumer can finish even when pxlead exceeds NWIN.
    wire gate2 = (wr1 > s2_pxlead) || (wr1 == NWIN);

    stage_seq #(.KSIZE(KSIZE), .P(P2), .PAD(PAD), .OW(OW), .OH(OW),
                .NWIN(NWIN), .SRC_PADDED(0), .STRIDE(OW),
                .AW(AW_M), .G(G2), .LEAD(LEAD+MARGIN)) u_seq2 (
        .clk(clk), .rst(rst), .start(start), .gate(gate2),
        .run(s2_run), .first(s2_first), .last(s2_last), .px(s2_pxi),
        .wbase(s2_wbase), .lane_v(s2_lv), .base(s2_base), .pxlead(s2_pxlead), .active(s2_active)
    );

    fmap_bank #(.W(FWA), .N(NWIN), .P(P2), .AW(AW_M)) u_fmapA (
        .clk(clk), .we(s1_we), .waddr(s1_px[AW_M-1:0]), .wdata(s1_data),
        .rbase(s2_base), .rq(s2_q)
    );

    conv_engine_v2 #(.CIN(CH), .COUT(CH), .KSIZE(KSIZE), .P(P2),
                     .IN_W(A1_W), .W_W(W_W), .ACC_W(ACC2_W), .OUT_W(A2_W),
                     .SRC_BINARY(0), .DO_RELU(1), .DSP_LANES(DSP_L2), .WROM_BLOCK(WROM_BLOCK),
                     .WFILE(WFILE2), .BFILE(BFILE2)) u_l2 (
        .clk(clk), .rst(rst), .run(s2_run), .first(s2_first), .last(s2_last),
        .px(s2_pxi), .wbase(s2_wbase), .lane_v(s2_lv), .src_q(s2_q),
        .res_we(s2_we), .res_px(s2_px), .res_data(s2_data), .res_sign()
    );

    wire [AW_M-1:0]   s3_base;
    wire [P3*FWB-1:0] s3_q;
    wire              s3_run, s3_first, s3_last, s3_active;
    wire [7:0]        s3_wbase;
    wire [P3-1:0]     s3_lv;
    wire [15:0]       s3_pxi, s3_pxlead;
    wire [15:0]       s3_unused;

    wire gate3 = (wr2 > s3_pxlead) || (wr2 == NWIN);

    stage_seq #(.KSIZE(KSIZE), .P(P3), .PAD(PAD), .OW(OW), .OH(OW),
                .NWIN(NWIN), .SRC_PADDED(0), .STRIDE(OW),
                .AW(AW_M), .G(G3), .LEAD(LEAD+MARGIN)) u_seq3 (
        .clk(clk), .rst(rst), .start(start), .gate(gate3),
        .run(s3_run), .first(s3_first), .last(s3_last), .px(s3_pxi),
        .wbase(s3_wbase), .lane_v(s3_lv), .base(s3_base), .pxlead(s3_pxlead), .active(s3_active)
    );

    fmap_bank #(.W(FWB), .N(NWIN), .P(P3), .AW(AW_M)) u_fmapB (
        .clk(clk), .we(s2_we), .waddr(s2_px[AW_M-1:0]), .wdata(s2_data),
        .rbase(s3_base), .rq(s3_q)
    );

    conv_engine_v2 #(.CIN(CH), .COUT(2), .KSIZE(KSIZE), .P(P3),
                     .IN_W(A2_W), .W_W(W_W), .ACC_W(ACC3_W), .OUT_W(8),
                     .SRC_BINARY(0), .DO_RELU(0), .DSP_LANES(DSP_L3), .WROM_BLOCK(WROM_BLOCK),
                     .WFILE(WFILE3), .BFILE(BFILE3)) u_l3 (
        .clk(clk), .rst(rst), .run(s3_run), .first(s3_first), .last(s3_last),
        .px(s3_pxi), .wbase(s3_wbase), .lane_v(s3_lv), .src_q(s3_q),
        .res_we(s3_we), .res_px(s3_px), .res_data(s3_unused),
        .res_sign(s3_sign)
    );

    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0; done <= 1'b0; latency_cycles <= 32'd0;
        end else if (start) begin
            busy <= 1'b1; done <= 1'b0; latency_cycles <= 32'd0;
        end else if (busy) begin
            latency_cycles <= latency_cycles + 32'd1;
            if (s3_we && (s3_px == NWIN-1)) begin
                busy <= 1'b0;
                done <= 1'b1;
            end
        end
    end

    // Bit zero is the X prediction; bit one is the Z prediction.
    // Read addresses are sampled on a rising edge.
    reg [1:0] decram [0:NWIN-1];
    always @(posedge clk) begin
        if (s3_we) decram[s3_px[AW_M-1:0]] <= s3_sign;
        dec_q <= decram[dec_raddr[AW_M-1:0]];
    end
endmodule
