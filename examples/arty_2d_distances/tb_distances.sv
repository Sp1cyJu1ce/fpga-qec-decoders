// Compare complete predictions from successive frames without resetting.
// The separate d=5 fixture also checks the raw sums and hidden activations.
`timescale 1ns/1ps
module tb_distances;
    parameter integer D = 9, P2 = 4, NF = 3, EXPECT = 12561;
    localparam integer SIDE = 2*D-1, N = SIDE*SIDE;
    localparam integer INPUT_WORDS = (SIDE+10)*(SIDE+10);
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1, start = 0, syn_we = 0;
    reg [15:0] syn_addr = 0, dec_raddr = 0;
    reg [3:0] syn_nib = 0;
    wire busy, done;
    wire [31:0] latency_cycles;
    wire [1:0] dec_q;
    decoder_top_v2 #(.D(D), .P1(11), .P2(P2), .P3(6),
                     .DSP_L2(1), .DSP_L3(6)) dut(.*);
    reg [3:0] inputs [0:NF*INPUT_WORDS-1];
    reg [1:0] decisions [0:NF*N-1];
    integer frame, pixel, mismatches;

    initial begin
        $readmemh("inputs.hex", inputs);
        $readmemh("decisions.hex", decisions);
        repeat (5) @(negedge clk);
        rst = 0;
        for (frame = 0; frame < NF; frame = frame + 1) begin
            for (pixel = 0; pixel < INPUT_WORDS; pixel = pixel + 1) begin
                @(negedge clk);
                syn_we = 1; syn_addr = pixel; syn_nib = inputs[frame*INPUT_WORDS+pixel];
            end
            @(negedge clk); syn_we = 0; start = 1;
            @(negedge clk); start = 0;
            wait (done === 1'b1);
            @(negedge clk);
            mismatches = 0;
            for (pixel = 0; pixel < N; pixel = pixel + 1) begin
                dec_raddr = pixel;
                @(negedge clk);
                if (dec_q !== decisions[frame*N+pixel]) mismatches = mismatches + 1;
            end
            if (latency_cycles != EXPECT) $fatal(1, "Unexpected core cycle count");
            if (mismatches != 0) $fatal(1, "Prediction mismatches: %0d", mismatches);
            $display("DISTANCE_FRAME frame=%0d d=%0d cycles=%0d decision_bits=%0d mismatches=%0d",
                     frame, D, latency_cycles, 2*N, mismatches);
        end
        $display("DISTANCE_PASS frames=%0d", NF);
        $finish;
    end

    initial begin
        repeat (NF*(INPUT_WORDS+N+2*EXPECT+100)) @(posedge clk);
        $fatal(1, "Simulation watchdog");
    end
endmodule
