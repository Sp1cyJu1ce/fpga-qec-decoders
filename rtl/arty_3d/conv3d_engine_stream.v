// One 3x3x3 integer convolution engine.
// Traversal order is output-channel group, kernel tap, input-channel group.
// Source and weight reads feed registered products, a reduction tree and an
// accumulator. Out-of-volume taps contribute zero.
//
// Hidden outputs use ReLU, integer multiply/shift with rounding, then uint8
// saturation. The output layer adds the binary skip input and emits logit>0.
// The parent schedules complete voxels and owns feature-map storage.
`timescale 1ns / 1ps

module conv3d_engine_stream #(
    parameter integer CIN        = 128,
    parameter integer COUT       = 128,
    // TCI and TCO are simultaneous input/output channels, not bit widths.
    parameter integer TCI        = 8,
    parameter integer TCO        = 16,
    parameter integer WEIGHT_W   = 4,
    parameter integer SRC_BINARY = 0,
    parameter integer SKIP_CIN   = 0,
    parameter integer SKIP_W     = 16,
    parameter integer DO_REQUANT = 1,
    parameter integer RQ_SHIFT   = 15,
    parameter integer NT = 6, NR = 4, NC = 4,
    parameter integer ACT_W      = 8,
    parameter integer ACC_W      = 32,
    parameter WFILE  = "L2_weights.hex",
    parameter BFILE  = "L2_B.hex",
    parameter MFILE  = "L2_M.hex",
    parameter SKFILE = "L4_skip.hex",

    parameter integer CIGN  = (SRC_BINARY != 0) ? 1 : CIN / TCI,
    parameter integer CIGE  = CIGN + ((SKIP_CIN > 0) ? 1 : 0),
    parameter integer OCGN  = COUT / TCO,
    parameter integer WWORD = TCO * ((SRC_BINARY != 0) ? CIN : TCI) * WEIGHT_W,
    parameter integer WDEPTH= OCGN * 27 * CIGN,
    parameter integer SRCW  = (SRC_BINARY != 0) ? CIN : TCI * ACT_W
)(
    input  wire                    clk,
    input  wire                    rst,

    input  wire                    slot_start,
    input  wire                    run,
    input  wire [15:0]             px,
    input  wire [7:0]              vt,
    input  wire [7:0]              vr,
    input  wire [7:0]              vc,

    output wire [15:0]             src_addr,
    output wire [4:0]              src_cig,
    input  wire [SRCW-1:0]         src_q,
    input  wire [(SKIP_CIN>0?SKIP_CIN:1)-1:0] skip_q,

    output reg                     res_we,
    output reg  [15:0]             res_px,
    output reg  [7:0]              res_ocg,
    output reg  [TCO*ACT_W-1:0]    res_data,
    output reg  [COUT-1:0]         res_dec
);
    localparam integer NCI   = (SRC_BINARY != 0) ? CIN : TCI;
    localparam integer PW    = (SRC_BINARY != 0) ? WEIGHT_W + 1
                                                 : ACT_W + WEIGHT_W + 1;
    localparam integer SKPW  = SKIP_W + 1;
    localparam integer RNDW  = 48;

    reg [WWORD-1:0] wmem [0:WDEPTH-1];
    reg [31:0]      birom [0:COUT-1];
    initial begin
        $readmemh(WFILE, wmem);
        $readmemh(BFILE, birom);
    end

    reg signed [1:0] dtr [0:26];
    reg signed [1:0] dhr [0:26];
    reg signed [1:0] dwr [0:26];
    integer ip;
    initial for (ip = 0; ip < 27; ip = ip + 1) begin
        dtr[ip] = (ip / 9) - 1;
        dhr[ip] = ((ip / 3) % 3) - 1;
        dwr[ip] = (ip % 3) - 1;
    end

    localparam integer TOT = OCGN * 27 * CIGE;

    reg [12:0] cyc;
    reg [4:0]  cig;
    reg [4:0]  tap;
    reg [4:0]  ocg;
    reg [12:0] wa;
    wire       busy = (cyc < TOT);

    always @(posedge clk) begin
        if (rst | slot_start) begin
            cyc <= 13'd0; cig <= 5'd0; tap <= 5'd0; ocg <= 5'd0; wa <= 13'd0;
        end else if (busy && run) begin
            cyc <= cyc + 13'd1;
            if (cig != CIGN || SKIP_CIN == 0) wa <= wa + 13'd1;
            if (cig == CIGE - 1) begin
                cig <= 5'd0;
                if (tap == 5'd26) begin
                    tap <= 5'd0;
                    ocg <= ocg + 5'd1;
                end else tap <= tap + 5'd1;
            end else cig <= cig + 5'd1;
        end
    end

    // The extra input group carries skip channels and does not advance wa.
    wire a_skip  = (SKIP_CIN > 0) && (cig == CIGN);
    wire a_first = (tap == 5'd0) && (cig == 5'd0);
    wire a_glast = (tap == 5'd26) && (cig == CIGE - 1);

    wire signed [8:0] st = $signed({1'b0, vt}) + dtr[tap];
    wire signed [8:0] sr = $signed({1'b0, vr}) + dhr[tap];
    wire signed [8:0] sc = $signed({1'b0, vc}) + dwr[tap];
    wire in_b = (st >= 0) && (st < NT) && (sr >= 0) && (sr < NR)
             && (sc >= 0) && (sc < NC);
    assign src_addr = in_b ? (st[7:0] * NR + sr[7:0]) * NC + sc[7:0] : 16'd0;
    assign src_cig  = cig;

    reg        b_v, b_run, b_first, b_glast, b_skip, c_run, c_first, c_glast, d_run, d_first, d_glast;
    reg [4:0]  b_tap, b_ocg, c_ocg, d_ocg;
    reg [15:0] b_px, c_px, d_px;
    always @(posedge clk) begin
        if (rst) begin
            {b_v, b_run, b_first, b_glast, b_skip} <= 5'd0;
            {c_run, c_first, c_glast} <= 3'd0;
            {d_run, d_first, d_glast} <= 3'd0;
            b_tap <= 5'd0; b_ocg <= 5'd0; c_ocg <= 5'd0; d_ocg <= 5'd0;
            b_px <= 16'd0; c_px <= 16'd0; d_px <= 16'd0;
        end else begin
            b_v     <= in_b && busy;         b_run   <= run && busy;
            b_first <= a_first && busy;      b_glast <= a_glast && busy;
            b_skip  <= a_skip;
            b_tap   <= tap;  b_ocg <= ocg;   b_px    <= px;
            c_run   <= b_run;  c_first <= b_first;  c_glast <= b_glast;
            c_ocg   <= b_ocg;  c_px    <= b_px;
            d_run   <= c_run;  d_first <= c_first;  d_glast <= c_glast;
            d_ocg   <= c_ocg;  d_px    <= c_px;
        end
    end

    reg [WWORD-1:0] wq;
    always @(posedge clk) wq <= wmem[wa];

    wire [TCO*NCI*PW-1:0] prod_bus;

    genvar go, gc;
    generate
      for (go = 0; go < TCO; go = go + 1) begin: PO
        for (gc = 0; gc < NCI; gc = gc + 1) begin: PC
            wire signed [WEIGHT_W-1:0] w =
                $signed(wq[(go*NCI + gc)*WEIGHT_W +: WEIGHT_W]);
            reg signed [PW-1:0] p;
            if (SRC_BINARY != 0) begin: G_BIN

                wire bitv = src_q[gc] & b_v;
                always @(posedge clk)
                    p <= bitv ? {{(PW-WEIGHT_W){w[WEIGHT_W-1]}}, w} : {PW{1'b0}};
            end else begin: G_MUL

                // uint8 activations need a leading zero before signed multiplication.
                wire signed [ACT_W:0]  a    = $signed({1'b0, src_q[gc*ACT_W +: ACT_W]});
                wire signed [PW-1:0]   mres = a * w;
                always @(posedge clk) p <= b_v ? mres : {PW{1'b0}};
            end
            assign prod_bus[(go*NCI + gc)*PW +: PW] = p;
        end
      end
    endgenerate

    wire [TCO*((SKIP_CIN>0?SKIP_CIN:1))*SKPW-1:0] sprod_bus;
    generate if (SKIP_CIN > 0) begin: G_SKIP
        reg [TCO*SKIP_CIN*SKIP_W-1:0] skrom [0:26];
        initial $readmemh(SKFILE, skrom);
        wire [TCO*SKIP_CIN*SKIP_W-1:0] skw = skrom[b_tap];
        genvar so, sc2;
        for (so = 0; so < TCO; so = so + 1) begin: SO
            for (sc2 = 0; sc2 < SKIP_CIN; sc2 = sc2 + 1) begin: SC
                wire signed [SKIP_W-1:0] w =
                    $signed(skw[(so*SKIP_CIN + sc2)*SKIP_W +: SKIP_W]);
                reg signed [SKPW-1:0] p;
                always @(posedge clk)
                    p <= (skip_q[sc2] & b_v & b_skip)
                         ? {w[SKIP_W-1], w} : {SKPW{1'b0}};
                assign sprod_bus[(so*SKIP_CIN + sc2)*SKPW +: SKPW] = p;
            end
        end
    end else begin: G_NOSKIP
        assign sprod_bus = 0;
    end endgenerate

    reg b2_skip;
    always @(posedge clk) b2_skip <= b_skip;

    reg  signed [ACC_W-1:0] acc  [0:TCO-1];
    wire signed [ACC_W-1:0] acc_final [0:TCO-1];

    // Reduce at the product-sum width, then sign-extend once into ACC_W.
    localparam integer NSUM   = (SKIP_CIN > NCI) ? SKIP_CIN : NCI;
    localparam integer PWMAX  = (SKIP_CIN > 0 && SKPW > PW) ? SKPW : PW;
    localparam integer TREE_W = PWMAX + ((NSUM <= 1) ? 0 :
                                (NSUM <= 2) ? 1 : (NSUM <= 4) ? 2 :
                                (NSUM <= 8) ? 3 : (NSUM <= 16) ? 4 : 5);

    genvar gg;
    generate for (gg = 0; gg < TCO; gg = gg + 1) begin: ACCL
        (* use_dsp = "no" *) reg signed [TREE_W-1:0] psum;
        integer c;
        always @(*) begin
            psum = {TREE_W{1'b0}};
            if (b2_skip) begin
                for (c = 0; c < ((SKIP_CIN>0)?SKIP_CIN:1); c = c + 1)
                    psum = psum + $signed(sprod_bus[(gg*((SKIP_CIN>0)?SKIP_CIN:1)+c)*SKPW +: SKPW]);
            end else begin
                for (c = 0; c < NCI; c = c + 1)
                    psum = psum + $signed(prod_bus[(gg*NCI + c)*PW +: PW]);
            end
        end
        (* use_dsp = "no" *) reg signed [TREE_W-1:0] psum_r;
        always @(posedge clk) psum_r <= psum;
        wire signed [ACC_W-1:0] psum_x = {{(ACC_W-TREE_W){psum_r[TREE_W-1]}}, psum_r};

        wire signed [ACC_W-1:0] bias_v = $signed(birom[d_ocg*TCO + gg]);
        assign acc_final[gg] = (d_first ? bias_v : acc[gg]) + psum_x;
        always @(posedge clk) if (d_run) acc[gg] <= acc_final[gg];
    end endgenerate

    generate if (DO_REQUANT != 0) begin: G_RQ
        reg [15:0] mrom [0:COUT-1];
        initial $readmemh(MFILE, mrom);

        reg  signed [ACC_W-1:0] hold [0:TCO-1];
        reg  [15:0] h_px;
        reg  [4:0]  h_ocg;
        reg         rq_go;
        integer k;
        always @(posedge clk) begin
            rq_go <= 1'b0;
            if (d_glast && d_run) begin
                for (k = 0; k < TCO; k = k + 1) hold[k] <= acc_final[k];
                h_px <= d_px;  h_ocg <= d_ocg;  rq_go <= 1'b1;
            end
        end

        // The requantizer must finish a group before the next group arrives.
        // The packaged T32 hidden layers have 432 issue clocks per group.
        reg [7:0]  idx;
        reg        r_act;
        reg [24:0] q1_acc;  reg [17:0] q1_m;  reg q1_v;  reg [7:0] q1_i;
        reg signed [RNDW-1:0] q2_p;           reg q2_v;  reg [7:0] q2_i;
        reg [TCO*ACT_W-1:0] gather;
        wire signed [RNDW-1:0] rounded = q2_p + (48'sd1 <<< (RQ_SHIFT - 1));
        wire [RNDW-1:0] shifted = rounded >>> RQ_SHIFT;
        wire [ACT_W-1:0] clamped = (|shifted[RNDW-2:ACT_W]) ? {ACT_W{1'b1}}
                                                            : shifted[ACT_W-1:0];
        always @(posedge clk) begin
            if (rst) begin
                r_act <= 1'b0; q1_v <= 1'b0; q2_v <= 1'b0; res_we <= 1'b0;
                idx <= 8'd0; res_px <= 16'd0; res_ocg <= 8'd0; res_data <= 0;
            end else begin
                res_we <= 1'b0;
                if (rq_go) begin idx <= 8'd0; r_act <= 1'b1; end
                q1_v <= 1'b0;
                if (r_act) begin
                    q1_acc <= hold[idx][ACC_W-1] ? 25'd0 : hold[idx][24:0];
                    q1_m   <= {2'b00, mrom[h_ocg*TCO + idx]};
                    q1_i   <= idx;  q1_v <= 1'b1;
                    if (idx == TCO - 1) r_act <= 1'b0;
                    else idx <= idx + 8'd1;
                end
                q2_v <= q1_v;  q2_i <= q1_i;
                if (q1_v) q2_p <= $signed({1'b0, q1_acc}) * $signed(q1_m);
                if (q2_v) begin
                    gather[q2_i*ACT_W +: ACT_W] <= clamped;
                    if (q2_i == TCO - 1) begin
                        res_we   <= 1'b1;
                        res_px   <= h_px;
                        res_ocg  <= {3'd0, h_ocg};
                        res_data <= {clamped,
                                     gather[(TCO-1)*ACT_W-1:0]};
                    end
                end
            end
        end
        always @(posedge clk) res_dec <= {COUT{1'b0}};
    end else begin: G_SIGN
        integer k2;
        always @(posedge clk) begin
            if (rst) begin
                res_we <= 1'b0; res_px <= 16'd0; res_ocg <= 8'd0;
                res_dec <= {COUT{1'b0}}; res_data <= 0;
            end else begin
                res_we <= d_glast && d_run;
                if (d_glast && d_run) begin
                    res_px  <= d_px;
                    res_ocg <= {3'd0, d_ocg};
                    for (k2 = 0; k2 < TCO; k2 = k2 + 1)
                        res_dec[k2] <= (acc_final[k2] > 0);
                end
            end
        end
    end endgenerate

endmodule
