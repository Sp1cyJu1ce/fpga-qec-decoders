// One sparse distance-five fixture, checked at every layer and at readback.
// The clock is a simulation clock; this test does not establish board timing.
`timescale 1ns/1ps
module tb_smoke;
    localparam integer N = 81, INPUT_WORDS = 361;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1, start = 0, syn_we = 0;
    reg [15:0] syn_addr = 0, dec_raddr = 0;
    reg [3:0] syn_nib = 0;
    wire busy, done;
    wire [31:0] latency_cycles;
    wire [1:0] dec_q;
    decoder_top_v2 #(.D(5), .P1(11), .P2(4), .P3(6),
                     .DSP_L2(1), .DSP_L3(6)) dut(.*);

    reg [3:0] inputs [0:INPUT_WORDS-1];
    reg [143:0] raw1 [0:N-1];
    reg [215:0] raw2 [0:N-1];
    reg [71:0] raw3 [0:N-1];
    reg [98:0] act1 [0:N-1];
    reg [188:0] act2 [0:N-1];
    reg [1:0] decisions [0:N-1];
    integer raw_count1 = 0, raw_count2 = 0, raw_count3 = 0;
    integer act_count1 = 0, act_count2 = 0, act_count3 = 0;
    integer i;
    reg checking = 0;

    always @(posedge clk) if (checking && !rst) begin
        if (dut.u_l1.runp[5] && dut.u_l1.lastp[5]) begin
            if (dut.u_l1.pxp[5] != raw_count1) $fatal(1, "Layer 1 raw order");
            raw_count1 = raw_count1 + 1;
        end
        if (dut.u_l2.runp[5] && dut.u_l2.lastp[5]) begin
            if (dut.u_l2.pxp[5] != raw_count2) $fatal(1, "Layer 2 raw order");
            raw_count2 = raw_count2 + 1;
        end
        if (dut.u_l3.runp[5] && dut.u_l3.lastp[5]) begin
            if (dut.u_l3.pxp[5] != raw_count3) $fatal(1, "Layer 3 raw order");
            raw_count3 = raw_count3 + 1;
        end
        if (dut.s1_we) begin
            if (dut.s1_px != act_count1 || dut.s1_data !== act1[dut.s1_px])
                $fatal(1, "Layer 1 activation");
            act_count1 = act_count1 + 1;
        end
        if (dut.s2_we) begin
            if (dut.s2_px != act_count2 || dut.s2_data !== act2[dut.s2_px])
                $fatal(1, "Layer 2 activation");
            act_count2 = act_count2 + 1;
        end
        if (dut.s3_we) begin
            if (dut.s3_px != act_count3 || dut.s3_sign !== decisions[dut.s3_px])
                $fatal(1, "Layer 3 prediction");
            act_count3 = act_count3 + 1;
        end
    end

    // Inspect the signed raw sums before activation or sign conversion.
    generate
        for (genvar c = 0; c < 9; c = c + 1) begin: raw_hidden
            always @(posedge clk) if (checking && !rst) begin
                if (dut.u_l1.runp[5] && dut.u_l1.lastp[5] &&
                    dut.u_l1.OCH[c].total !== raw1[dut.u_l1.pxp[5]][c*16+:16])
                    $fatal(1, "Layer 1 raw sum, channel %0d", c);
                if (dut.u_l2.runp[5] && dut.u_l2.lastp[5] &&
                    dut.u_l2.OCH[c].total !== raw2[dut.u_l2.pxp[5]][c*24+:24])
                    $fatal(1, "Layer 2 raw sum, channel %0d", c);
            end
        end
        for (genvar c = 0; c < 2; c = c + 1) begin: raw_output
            always @(posedge clk) if (checking && !rst &&
                dut.u_l3.runp[5] && dut.u_l3.lastp[5] &&
                dut.u_l3.OCH[c].total !== raw3[dut.u_l3.pxp[5]][c*36+:36])
                $fatal(1, "Layer 3 raw sum, channel %0d", c);
        end
    endgenerate

    initial begin
        $readmemh("input.hex", inputs);
        $readmemh("layer1_raw.hex", raw1);
        $readmemh("layer2_raw.hex", raw2);
        $readmemh("layer3_raw.hex", raw3);
        $readmemh("layer1_activation.hex", act1);
        $readmemh("layer2_activation.hex", act2);
        $readmemh("decisions.hex", decisions);
        repeat (5) @(negedge clk);
        rst = 0;
        for (i = 0; i < INPUT_WORDS; i = i + 1) begin
            @(negedge clk);
            syn_we = 1; syn_addr = i; syn_nib = inputs[i];
        end
        @(negedge clk); syn_we = 0; start = 1;
        @(negedge clk); start = 0; checking = 1;
        wait (done === 1'b1);
        @(negedge clk); checking = 0;
        if (raw_count1 != N || raw_count2 != N || raw_count3 != N ||
            act_count1 != N || act_count2 != N || act_count3 != N)
            $fatal(1, "Incomplete output coverage");
        if (latency_cycles != 4377) $fatal(1, "Unexpected core cycle count");
        for (i = 0; i < N; i = i + 1) begin
            dec_raddr = i;
            @(negedge clk);
            if (dec_q !== decisions[i]) $fatal(1, "Readback pixel %0d", i);
        end
        $display("SMOKE_PASS 2d d=5 cycles=%0d raw_values=1620 activations_and_decisions=1620", latency_cycles);
        $finish;
    end
    initial begin
        repeat (20000) @(posedge clk);
        $fatal(1, "Simulation watchdog");
    end
endmodule
