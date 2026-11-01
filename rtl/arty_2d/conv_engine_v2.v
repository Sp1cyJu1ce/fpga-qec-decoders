// One 11x11 convolution layer and its row-aligned tap sequencer.
// Each clock issues up to P consecutive taps from one kernel row. Invalid
// lanes contribute zero; the final partial group still occupies a cycle.
// Source and weight reads take two clocks, followed by registered products,
// lane sums, group sums, accumulation and the result write.
//
// Weights and activations are signed. Keep multiplication on its own signed
// wire before the validity mux, so negative weights retain their sign.
`timescale 1ns / 1ps

module conv_engine_v2 #(
    parameter integer CIN        = 9,
    parameter integer COUT       = 9,
    parameter integer KSIZE      = 11,
    parameter integer P          = 4,
    parameter integer IN_W       = 11,
    parameter integer W_W        = 8,
    parameter integer ACC_W      = 24,
    parameter integer OUT_W      = 21,
    parameter integer SRC_BINARY = 0,
    parameter integer DO_RELU    = 1,

    // Leading lanes use DSP multipliers; the remaining lanes use logic.
    parameter integer DSP_LANES  = 0,
    parameter integer WROM_BLOCK = 0,
    parameter         WFILE      = "L1_weights_par.hex",
    parameter         BFILE      = "L1_bias.hex",

    parameter integer SRC_CH_W   = (SRC_BINARY != 0) ? 1 : IN_W,
    parameter integer SRCW       = CIN * SRC_CH_W
)(
    input  wire                   clk,
    input  wire                   rst,

    input  wire                   run,
    input  wire                   first,
    input  wire                   last,
    input  wire [15:0]            px,
    input  wire [7:0]             wbase,
    input  wire [P-1:0]           lane_v,

    input  wire [P*SRCW-1:0]      src_q,

    output reg                    res_we,
    output reg  [15:0]            res_px,
    output reg  [COUT*OUT_W-1:0]  res_data,
    output reg  [COUT-1:0]        res_sign
);
    localparam integer KK    = KSIZE * KSIZE;
    localparam integer WWORD = COUT * CIN * W_W;
    localparam integer PW    = (SRC_BINARY != 0) ? W_W : (IN_W + W_W);

    // Bias files contain sign-extended 48-bit values; the trained values fit ACC_W.
    reg signed [ACC_W-1:0] b_mem [0:COUT-1];
    initial $readmemh(BFILE, b_mem);

    // Flags and output coordinates follow the arithmetic pipeline.
    reg [5:0]  runp, firstp, lastp;
    reg [15:0] pxp [0:5];
    integer d;
    always @(posedge clk) begin
        if (rst) begin
            runp <= 6'd0; firstp <= 6'd0; lastp <= 6'd0;
            for (d = 0; d < 6; d = d + 1) pxp[d] <= 16'd0;
        end else begin
            runp   <= {runp[4:0],   run};
            firstp <= {firstp[4:0], first};
            lastp  <= {lastp[4:0],  last};
            pxp[0] <= px;
            for (d = 1; d < 6; d = d + 1) pxp[d] <= pxp[d-1];
        end
    end

    reg  [WWORD-1:0] wq_r [0:P-1];
    wire [SRCW-1:0]  sq   [0:P-1];
    wire [P-1:0]     lv2;

    genvar gl;
    generate for (gl = 0; gl < P; gl = gl + 1) begin: LANE

        wire [7:0] wa = wbase + gl[7:0];
        reg [WWORD-1:0] wq;

        if (WROM_BLOCK != 0) begin: G_BROM
            (* rom_style = "block" *) reg [WWORD-1:0] wmem [0:KK-1];
            initial $readmemh(WFILE, wmem);
            always @(posedge clk) begin
                wq       <= wmem[wa[7:0]];
                wq_r[gl] <= wq;
            end
        end else begin: G_DROM
            (* rom_style = "distributed" *) reg [WWORD-1:0] wmem [0:KK-1];
            initial $readmemh(WFILE, wmem);
            always @(posedge clk) begin
                wq       <= wmem[wa[7:0]];
                wq_r[gl] <= wq;
            end
        end

        assign sq[gl] = src_q[gl*SRCW +: SRCW];

        reg v1, v2;
        always @(posedge clk) begin v1 <= lane_v[gl] && run; v2 <= v1; end
        assign lv2[gl] = v2;
    end endgenerate

    // Constant-index arrays keep the multiplier and reduction stages separate.
    reg signed [PW-1:0] prod [0:P*COUT*CIN-1];

    genvar gp, go, gc;
    generate
      for (gp = 0; gp < P; gp = gp + 1) begin: PL
        for (gc = 0; gc < CIN; gc = gc + 1) begin: PC

          wire signed [IN_W-1:0] a = (SRC_BINARY != 0) ? {IN_W{1'b0}}
                                   : $signed(sq[gp][gc*IN_W +: IN_W]);
          wire bitv = sq[gp][gc] & lv2[gp];
          for (go = 0; go < COUT; go = go + 1) begin: PO
            localparam integer IDX = (gp*COUT + go)*CIN + gc;
            wire signed [W_W-1:0] w =
                $signed(wq_r[gp][(go*CIN + gc)*W_W +: W_W]);
            if (SRC_BINARY != 0) begin: G_BIN

                always @(posedge clk)
                    prod[IDX] <= bitv ? w : {PW{1'b0}};
            end else if (gp < DSP_LANES) begin: G_DSP

                (* use_dsp = "yes" *) wire signed [PW-1:0] mres = a * w;
                always @(posedge clk)
                    prod[IDX] <= lv2[gp] ? mres : {PW{1'b0}};
            end else begin: G_LUT
                (* use_dsp = "no" *) wire signed [PW-1:0] mres = a * w;
                always @(posedge clk)
                    prod[IDX] <= lv2[gp] ? mres : {PW{1'b0}};
            end
          end
        end
      end
    endgenerate

    // Keep the reduction trees in logic; DSP use is selected at the products.
    (* use_dsp = "no" *) reg signed [ACC_W-1:0] lsum [0:COUT*P-1];
    (* use_dsp = "no" *) reg signed [ACC_W-1:0] gsum [0:COUT-1];
    (* use_dsp = "no" *) reg signed [ACC_W-1:0] acc  [0:COUT-1];
    wire [COUT*OUT_W-1:0]  res_data_w;
    wire [COUT-1:0]        res_sign_w;

    genvar gg, gl2;
    generate for (gg = 0; gg < COUT; gg = gg + 1) begin: OCH

        for (gl2 = 0; gl2 < P; gl2 = gl2 + 1) begin: LSUM
            reg signed [ACC_W-1:0] t;
            integer c;
            always @(posedge clk) begin
                t = {ACC_W{1'b0}};
                for (c = 0; c < CIN; c = c + 1)
                    t = t + prod[(gl2*COUT + gg)*CIN + c];
                lsum[gg*P + gl2] <= t;
            end
        end

        reg signed [ACC_W-1:0] tg;
        integer l;
        always @(posedge clk) begin
            tg = {ACC_W{1'b0}};
            for (l = 0; l < P; l = l + 1)
                tg = tg + lsum[gg*P + l];
            gsum[gg] <= tg;
        end

        always @(posedge clk)
            if (runp[4]) acc[gg] <= (firstp[4] ? {ACC_W{1'b0}} : acc[gg]) + gsum[gg];

        wire signed [ACC_W-1:0] total = acc[gg] + b_mem[gg];
        wire signed [ACC_W-1:0] act   = (DO_RELU != 0 && total[ACC_W-1])
                                        ? {ACC_W{1'b0}} : total;
        assign res_data_w[gg*OUT_W +: OUT_W] = act[OUT_W-1:0];
        assign res_sign_w[gg]                = (total > 0);
    end endgenerate

    always @(posedge clk) begin
        if (rst) begin
            res_we <= 1'b0; res_px <= 16'd0; res_data <= 0; res_sign <= 0;
        end else begin
            res_we <= runp[5] && lastp[5];
            if (runp[5] && lastp[5]) begin
                res_px   <= pxp[5];
                res_data <= res_data_w;
                res_sign <= res_sign_w;
            end
        end
    end
endmodule

// Raster-order output traversal. A low gate pauses issue, while the arithmetic
// pipeline drains its already-issued groups. Address increments avoid a
// multiply on the source-memory address path.
module stage_seq #(
    parameter integer KSIZE      = 11,
    parameter integer P          = 4,
    parameter integer PAD        = 5,
    parameter integer OW         = 59,
    parameter integer OH         = 59,
    parameter integer NWIN       = 3481,
    parameter integer SRC_PADDED = 0,
    parameter integer STRIDE     = 59,
    parameter integer AW         = 12,
    parameter integer G          = 3,
    parameter integer LEAD       = 300
)(
    input  wire            clk,
    input  wire            rst,
    input  wire            start,
    input  wire            gate,
    output wire            run,
    output wire            first,
    output wire            last,
    output reg  [15:0]     px,
    output wire [7:0]      wbase,
    output wire [P-1:0]    lane_v,
    output reg  [AW-1:0]   base,
    output reg  [15:0]     pxlead,
    output wire            active
);
    // Hidden maps use implicit zero padding; the input frame has an explicit halo.
    localparam signed [15:0] OFF0 = (SRC_PADDED != 0) ? 16'sd0
                                  : -(PAD * STRIDE + PAD);
    localparam [AW-1:0] STRIDE_W = STRIDE[AW-1:0];
    localparam [AW-1:0] P_W      = P[AW-1:0];
    localparam [7:0]    P_8      = P[7:0];
    localparam [7:0]    KSIZE_8  = KSIZE[7:0];

    reg  [15:0] pxb;
    reg  [7:0]  oh, ow;
    reg  [3:0]  kh, grp;
    reg  [7:0]  tapb;
    reg  [7:0]  krow;
    reg  signed [9:0] sr, sc0;
    reg  [AW-1:0] rowb;
    reg  run_en;

    wire lastgrp = (grp == G-1);
    wire lastrow = (kh == KSIZE-1);
    wire owlast  = (ow == OW-1);

    assign active = run_en && (px < NWIN);
    assign run    = active && gate;
    assign first  = (kh == 4'd0) && (grp == 4'd0);
    assign last   = lastgrp && lastrow;
    assign wbase  = krow + tapb;

    genvar j;
    generate for (j = 0; j < P; j = j + 1) begin: LV
        wire in_kern = ((tapb + j) < KSIZE);
        if (SRC_PADDED != 0) begin: G_PAD
            assign lane_v[j] = in_kern;
        end else begin: G_IMP
            wire signed [9:0] c = sc0 + j;
            assign lane_v[j] = in_kern && (sr >= 0) && (sr < OH)
                                       && (c  >= 0) && (c  < OW);
        end
    end endgenerate

    wire [15:0] pxb_n = (SRC_PADDED != 0)
                      ? (owlast ? pxb + (2*PAD + 1) : pxb + 16'd1)
                      : (pxb + 16'd1);
    wire [7:0]  oh_n  = owlast ? (oh + 8'd1) : oh;
    wire [7:0]  ow_n  = owlast ? 8'd0        : (ow + 8'd1);
    wire [AW-1:0] rowb_n = pxb_n + OFF0[AW-1:0];

    always @(posedge clk) begin
        if (rst || start) begin
            px <= 16'd0; pxlead <= LEAD[15:0]; pxb <= 16'd0; oh <= 8'd0; ow <= 8'd0;
            kh <= 4'd0;  grp <= 4'd0; tapb <= 8'd0; krow <= 8'd0;
            sr <= -PAD;  sc0 <= -PAD;
            base <= OFF0[AW-1:0]; rowb <= OFF0[AW-1:0];
            run_en <= start;
        end else if (run) begin
            if (!last) begin
                if (lastgrp) begin
                    grp  <= 4'd0;  tapb <= 8'd0;
                    kh   <= kh + 4'd1;
                    krow <= krow + KSIZE_8;
                    sr   <= sr + 10'sd1;
                    sc0  <= $signed({2'b00, ow}) - PAD;
                    rowb <= rowb + STRIDE_W;
                    base <= rowb + STRIDE_W;
                end else begin
                    grp  <= grp + 4'd1;
                    tapb <= tapb + P_8;
                    sc0  <= sc0 + P;
                    base <= base + P_W;
                end
            end else begin
                px   <= px + 16'd1;
                pxlead <= pxlead + 16'd1;
                pxb  <= pxb_n;  oh <= oh_n;  ow <= ow_n;
                kh   <= 4'd0;   grp <= 4'd0; tapb <= 8'd0; krow <= 8'd0;
                sr   <= $signed({2'b00, oh_n}) - PAD;
                sc0  <= $signed({2'b00, ow_n}) - PAD;
                rowb <= rowb_n; base <= rowb_n;
            end
        end
    end
endmodule
