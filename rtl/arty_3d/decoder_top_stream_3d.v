// Finite-volume 3D integer CNN with independent layer schedules.
// Input shape is (D+1) x ((D+3)/2) x ((D+3)/2), with twelve binary channels.
// Load every input voxel before start. All intermediate volumes remain in
// memory; this core is not a rolling-window quantum measurement interface.
// TCO_H changes hidden output-channel parallelism and requires matching ROM
// packing. The packaged Arty preset uses TCO_H=32 and shifts 15/20/19.
`timescale 1ns / 1ps

module decoder_top_stream_3d #(
    parameter integer D     = 5,

    parameter integer TCO_H = 16,
    parameter integer RQ_S1 = 15,
    parameter integer RQ_S2 = 20,
    parameter integer RQ_S3 = 19,
    parameter WF1 = "L1_weights.hex", parameter BF1 = "L1_B.hex", parameter MF1 = "L1_M.hex",
    parameter WF2 = "L2_weights.hex", parameter BF2 = "L2_B.hex", parameter MF2 = "L2_M.hex",
    parameter WF3 = "L3_weights.hex", parameter BF3 = "L3_B.hex", parameter MF3 = "L3_M.hex",
    parameter WF4 = "L4_weights.hex", parameter BF4 = "L4_B.hex", parameter SKF = "L4_skip.hex"
)(
    input  wire        clk,
    input  wire        rst,

    input  wire        syn_we,
    input  wire [15:0] syn_addr,
    input  wire [11:0] syn_word,

    input  wire        start,
    output reg         busy,
    output reg         done,
    output reg  [31:0] latency_cycles,

    input  wire [15:0] dec_raddr,
    output reg  [11:0] dec_q
);

    localparam integer NR   = (D + 3) / 2;
    localparam integer NTT  = D + 1;
    localparam integer NVOX = NTT * NR * NR;

    localparam integer SLOT_H = 27 * 16 * (128 / TCO_H);
    localparam integer SLOT   = (SLOT_H > 459) ? SLOT_H : 459;
    localparam integer LAG  = NR * NR + NR + 2;

    wire        s1_we, s2_we, s3_we;
    wire [15:0] s1_px, s2_px, s3_px;
    wire [7:0]  s1_ocg, s2_ocg, s3_ocg;
    reg [15:0] px1, px2, px3, px4;
    reg [7:0] t1,r1,c1,t2,r2,c2,t3,r3,c3,t4,r4,c4;
    reg [12:0] issue1,issue2,issue3,issue4;
    reg [15:0] written1,written2,written3;
    localparam integer LEAD = NR*NR+NR+1;
    localparam integer TOT1=432, TOTH=27*16*(128/TCO_H), TOT4=459;
    // Clip each axis separately to find the last producer voxel needed.
    function [15:0] max_neighbor;
        input [7:0] t,r,c;
        integer mt,mr,mc;
        begin
            mt=(t+1<NTT)?t+1:NTT-1;
            mr=(r+1<NR)?r+1:NR-1;
            mc=(c+1<NR)?c+1:NR-1;
            max_neighbor=(mt*NR+mr)*NR+mc;
        end
    endfunction
    wire ready2 = written1 > max_neighbor(t2,r2,c2);
    wire ready3 = written2 > max_neighbor(t3,r3,c3);
    wire ready4 = written3 > max_neighbor(t4,r4,c4);
    wire act1=busy && (px1<NVOX);
    wire act2=busy && (px2<NVOX) && ready2;
    wire act3=busy && (px3<NVOX) && ready3;
    wire act4=busy && (px4<NVOX) && ready4;
    wire last1=act1 && issue1==TOT1-1;
    wire last2=act2 && issue2==TOTH-1;
    wire last3=act3 && issue3==TOTH-1;
    wire last4=act4 && issue4==TOT4-1;
    wire sync1=start|last1, sync2=start|last2;
    wire sync3=start|last3, sync4=start|last4;
    wire s4_we;
    wire [15:0] s4_px;
    wire [11:0] s4_dec;
    `define ADVANCE(px,t,r,c) \
        begin \
            px <= px+16'd1; \
            if(c==NR-1) begin \
                c<=0; \
                if(r==NR-1) begin r<=0;t<=t+8'd1;end \
                else r<=r+8'd1; \
            end else c<=c+8'd1; \
        end
    always @(posedge clk) begin
        if(rst || start) begin
            busy<=!rst; done<=0; latency_cycles<=0;
            px1<=0;px2<=0;px3<=0;px4<=0;
            t1<=0;r1<=0;c1<=0;t2<=0;r2<=0;c2<=0;
            t3<=0;r3<=0;c3<=0;t4<=0;r4<=0;c4<=0;
            issue1<=0;issue2<=0;issue3<=0;issue4<=0;
            written1<=0;written2<=0;written3<=0;
        end else if(busy) begin
            latency_cycles<=latency_cycles+32'd1;

            // A voxel is ready only after its last output-channel group commits.
            if(s1_we && s1_ocg==15) written1<=s1_px+16'd1;
            if(s2_we && s2_ocg==(128/TCO_H)-1) written2<=s2_px+16'd1;
            if(s3_we && s3_ocg==(128/TCO_H)-1) written3<=s3_px+16'd1;
            if(act1) begin
                if(last1) begin issue1<=0;`ADVANCE(px1,t1,r1,c1) end
                else issue1<=issue1+13'd1;
            end
            if(act2) begin
                if(last2) begin issue2<=0;`ADVANCE(px2,t2,r2,c2) end
                else issue2<=issue2+13'd1;
            end
            if(act3) begin
                if(last3) begin issue3<=0;`ADVANCE(px3,t3,r3,c3) end
                else issue3<=issue3+13'd1;
            end
            if(act4) begin
                if(last4) begin issue4<=0;`ADVANCE(px4,t4,r4,c4) end
                else issue4<=issue4+13'd1;
            end
            if(s4_we && s4_px==NVOX-1) begin busy<=0;done<=1;end
        end
    end
    `undef ADVANCE

    wire [15:0] s1_addr, s4_addr;
    wire [4:0]  s1_cig_unused, s4_cig;
    reg  [11:0] in_q1, in_q4;

    // Two input copies serve the first layer and the final-layer skip path.
    reg [11:0] inram1 [0:NVOX-1];
    reg [11:0] inram4 [0:NVOX-1];
    always @(posedge clk) begin
        if (syn_we) begin
            inram1[syn_addr] <= syn_word;
            inram4[syn_addr] <= syn_word;
        end
        in_q1 <= inram1[s1_addr];
        in_q4 <= inram4[s4_addr];
    end

    // Each hidden map has sixteen 64-bit banks: eight channels per bank.
    // A wider producer writes more consecutive banks in the same group.
    localparam integer NBANK = 16;
    localparam integer WPG1  = 8   / 8;
    localparam integer WPGH  = TCO_H / 8;

    wire [TCO_H*8-1:0] s2_data, s3_data;
    wire [63:0]        s1_data;

    wire [15:0] s2_addr, s3_addr;
    wire [4:0]  s2_cig, s3_cig;

    wire [NBANK*64-1:0] fa_flat, fb_flat, fc_flat;
    reg  [3:0] a_sel, b_sel, c_sel;
    always @(posedge clk) begin
        a_sel <= s2_cig[3:0];
        b_sel <= s3_cig[3:0];
        c_sel <= s4_cig[3:0];
    end

    genvar bk;
    generate for (bk = 0; bk < NBANK; bk = bk + 1) begin: FMAP
        reg [63:0] mema [0:NVOX-1];
        reg [63:0] memb [0:NVOX-1];
        reg [63:0] memc [0:NVOX-1];
        reg [63:0] qa, qb, qc;
        always @(posedge clk) begin
            if (s1_we && (s1_ocg == bk / WPG1))
                mema[s1_px] <= s1_data[(bk % WPG1)*64 +: 64];
            if (s2_we && (s2_ocg == bk / WPGH))
                memb[s2_px] <= s2_data[(bk % WPGH)*64 +: 64];
            if (s3_we && (s3_ocg == bk / WPGH))
                memc[s3_px] <= s3_data[(bk % WPGH)*64 +: 64];
            qa <= mema[s2_addr];
            qb <= memb[s3_addr];
            qc <= memc[s4_addr];
        end
        assign fa_flat[bk*64 +: 64] = qa;
        assign fb_flat[bk*64 +: 64] = qb;
        assign fc_flat[bk*64 +: 64] = qc;
    end endgenerate

    wire [63:0] s2_q = fa_flat[a_sel*64 +: 64];
    wire [63:0] s3_q = fb_flat[b_sel*64 +: 64];
    wire [63:0] s4_q = fc_flat[c_sel*64 +: 64];

    conv3d_engine_stream #(
        .CIN(12), .COUT(128), .TCI(12), .TCO(8), .WEIGHT_W(8),
        .SRC_BINARY(1), .SKIP_CIN(0), .DO_REQUANT(1), .RQ_SHIFT(RQ_S1),
        .NT(NTT), .NR(NR), .NC(NR),
        .WFILE(WF1), .BFILE(BF1), .MFILE(MF1)
    ) u_l1 (
        .clk(clk), .rst(rst), .slot_start(sync1), .run(act1),
        .px(px1), .vt(t1), .vr(r1), .vc(c1),
        .src_addr(s1_addr), .src_cig(s1_cig_unused), .src_q(in_q1),
        .skip_q(1'b0),
        .res_we(s1_we), .res_px(s1_px), .res_ocg(s1_ocg),
        .res_data(s1_data), .res_dec()
    );

    conv3d_engine_stream #(
        .CIN(128), .COUT(128), .TCI(8), .TCO(TCO_H), .WEIGHT_W(4),
        .SRC_BINARY(0), .SKIP_CIN(0), .DO_REQUANT(1), .RQ_SHIFT(RQ_S2),
        .NT(NTT), .NR(NR), .NC(NR),
        .WFILE(WF2), .BFILE(BF2), .MFILE(MF2)
    ) u_l2 (
        .clk(clk), .rst(rst), .slot_start(sync2), .run(act2),
        .px(px2), .vt(t2), .vr(r2), .vc(c2),
        .src_addr(s2_addr), .src_cig(s2_cig), .src_q(s2_q),
        .skip_q(1'b0),
        .res_we(s2_we), .res_px(s2_px), .res_ocg(s2_ocg),
        .res_data(s2_data), .res_dec()
    );

    conv3d_engine_stream #(
        .CIN(128), .COUT(128), .TCI(8), .TCO(TCO_H), .WEIGHT_W(4),
        .SRC_BINARY(0), .SKIP_CIN(0), .DO_REQUANT(1), .RQ_SHIFT(RQ_S3),
        .NT(NTT), .NR(NR), .NC(NR),
        .WFILE(WF3), .BFILE(BF3), .MFILE(MF3)
    ) u_l3 (
        .clk(clk), .rst(rst), .slot_start(sync3), .run(act3),
        .px(px3), .vt(t3), .vr(r3), .vc(c3),
        .src_addr(s3_addr), .src_cig(s3_cig), .src_q(s3_q),
        .skip_q(1'b0),
        .res_we(s3_we), .res_px(s3_px), .res_ocg(s3_ocg),
        .res_data(s3_data), .res_dec()
    );

    wire [11:0] s4_dec_full;
    conv3d_engine_stream #(
        .CIN(128), .COUT(12), .TCI(8), .TCO(12), .WEIGHT_W(8),
        .SRC_BINARY(0), .SKIP_CIN(12), .SKIP_W(16), .DO_REQUANT(0),
        .NT(NTT), .NR(NR), .NC(NR),
        .WFILE(WF4), .BFILE(BF4), .SKFILE(SKF)
    ) u_l4 (
        .clk(clk), .rst(rst), .slot_start(sync4), .run(act4),
        .px(px4), .vt(t4), .vr(r4), .vc(c4),
        .src_addr(s4_addr), .src_cig(s4_cig), .src_q(s4_q),
        .skip_q(in_q4),
        .res_we(s4_we), .res_px(s4_px), .res_ocg(),
        .res_data(), .res_dec(s4_dec_full)
    );
    assign s4_dec = s4_dec_full;

    // One twelve-bit prediction word per voxel; readback is synchronous.
    reg [11:0] decram [0:NVOX-1];
    always @(posedge clk) begin
        if (s4_we) decram[s4_px] <= s4_dec;
        dec_q <= decram[dec_raddr];
    end

endmodule
