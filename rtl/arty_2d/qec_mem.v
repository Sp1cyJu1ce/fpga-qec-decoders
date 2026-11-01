// Two-clock source memories for row-aligned tap groups.
// mem_rep provides P copies for the narrow binary input frame. fmap_bank
// stores a hidden map once across NB banks, where NB is the next power of
// two at least P. Thus P itself need not be a power of two (P=6 is used).
// Bank padding and selection logic still consume resources.
`timescale 1ns / 1ps

// Lane j reads rbase+j. Every replica receives the same writes.
module mem_rep #(
    parameter integer W  = 4,
    parameter integer N  = 4761,
    parameter integer P  = 11,
    parameter integer AW = 13
)(
    input  wire            clk,
    input  wire            we,
    input  wire [AW-1:0]   waddr,
    input  wire [W-1:0]    wdata,
    input  wire [AW-1:0]   rbase,
    output wire [P*W-1:0]  rq
);
    genvar j;
    generate for (j = 0; j < P; j = j + 1) begin: R
        reg [W-1:0] mem [0:N-1];
        reg [W-1:0] q, q2;
        wire [AW-1:0] a = rbase + j[AW-1:0];
        always @(posedge clk) begin
            if (we) mem[waddr] <= wdata;
            q  <= mem[a];
            q2 <= q;
        end
        assign rq[j*W +: W] = q2;
    end endgenerate
endmodule

// Consecutive lane addresses occupy distinct banks. P<=NB ensures that
// each physical bank needs only one read per clock. Invalid padded reads
// are masked by the convolution engine rather than used as activations.
module fmap_bank #(
    parameter integer W  = 99,
    parameter integer N  = 3481,
    parameter integer P  = 4,
    parameter integer AW = 12
)(
    input  wire            clk,
    input  wire            we,
    input  wire [AW-1:0]   waddr,
    input  wire [W-1:0]    wdata,
    input  wire [AW-1:0]   rbase,
    output wire [P*W-1:0]  rq
);
    function integer clog2; input integer v; integer i; begin
        clog2 = 0; for (i = v-1; i > 0; i = i >> 1) clog2 = clog2 + 1; end
    endfunction

    localparam integer NB  = (P <= 1) ? 1 : (1 << clog2(P));
    localparam integer LP  = (P <= 1) ? 0 : clog2(NB);
    localparam integer DP  = (N + NB - 1) / NB;
    localparam integer DAW = (AW > LP) ? (AW - LP) : 1;

    generate
    if (P <= 1) begin: G_SINGLE
        reg [W-1:0] mem [0:N-1];
        reg [W-1:0] q, q2;
        always @(posedge clk) begin
            if (we) mem[waddr] <= wdata;
            q  <= mem[rbase];
            q2 <= q;
        end
        assign rq = q2;
    end else begin: G_BANKED
        wire [LP-1:0]  r  = rbase[LP-1:0];
        wire [DAW-1:0] qb = rbase[AW-1:LP];
        wire [NB*W-1:0] bq;

        genvar i;
        for (i = 0; i < NB; i = i + 1) begin: BK
            reg [W-1:0] mem [0:DP-1];
            reg [W-1:0] q, q2;

            wire [DAW-1:0] ai = qb + ((i[LP-1:0] < r) ? 1'b1 : 1'b0);
            always @(posedge clk) begin
                if (we && (waddr[LP-1:0] == i[LP-1:0]))
                    mem[waddr[AW-1:LP]] <= wdata;
                q  <= mem[ai];
                q2 <= q;
            end
            assign bq[i*W +: W] = q2;
        end

        // Delay the bank rotation by the same two clocks as the data.
        reg [LP-1:0] r1, r2;
        always @(posedge clk) begin r1 <= r; r2 <= r1; end

        genvar k;
        for (k = 0; k < P; k = k + 1) begin: LN
            wire [LP-1:0] sel = r2 + k[LP-1:0];
            assign rq[k*W +: W] = bq[sel*W +: W];
        end
    end
    endgenerate
endmodule
