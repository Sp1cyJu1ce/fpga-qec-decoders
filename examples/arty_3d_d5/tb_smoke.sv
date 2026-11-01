// One finite distance-five volume with every raw sum and output checked.
// The T32 packing matches the 95 MHz board preset; this is a core-only test.
`timescale 1ns/1ps
module tb_smoke;
    localparam integer N = 96;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1, start = 0, syn_we = 0;
    reg [15:0] syn_addr = 0, dec_raddr = 0;
    reg [11:0] syn_word = 0;
    wire busy, done;
    wire [31:0] latency_cycles;
    wire [11:0] dec_q;
    decoder_top_stream_3d #(.D(5), .TCO_H(32)) dut(.*);

    reg [11:0] inputs [0:N-1];
    reg [4095:0] raw1 [0:N-1], raw2 [0:N-1], raw3 [0:N-1];
    reg [383:0] raw4 [0:N-1];
    reg [1023:0] act1 [0:N-1], act2 [0:N-1], act3 [0:N-1];
    reg [11:0] act4 [0:N-1];
    integer raw_count [1:4], act_count [1:4];
    integer committed1 = 0, committed2 = 0, committed3 = 0;
    integer i, channel;
    reg checking = 0;

    always @(posedge clk) if (checking && !rst && !start) begin
        // Check reads before counting writes on this edge. A simultaneous
        // producer write cannot supply an earlier synchronous read request.
        if (dut.act2 && dut.u_l2.busy && dut.u_l2.in_b && dut.s2_addr >= committed1)
            $fatal(1, "Layer 2 read before producer commit");
        if (dut.act3 && dut.u_l3.busy && dut.u_l3.in_b && dut.s3_addr >= committed2)
            $fatal(1, "Layer 3 read before producer commit");
        if (dut.act4 && dut.u_l4.busy && dut.u_l4.in_b && dut.s4_addr >= committed3)
            $fatal(1, "Layer 4 read before producer commit");

        if (dut.u_l1.d_run && dut.u_l1.d_glast) begin
            if (dut.u_l1.d_px*128 + dut.u_l1.d_ocg*8 != raw_count[1])
                $fatal(1, "Layer 1 raw order");
            for (channel = 0; channel < 8; channel = channel + 1) begin
                if (dut.u_l1.acc_final[channel] !==
                    raw1[dut.u_l1.d_px][(dut.u_l1.d_ocg*8+channel)*32+:32])
                    $fatal(1, "Layer 1 raw sum, voxel %0d channel %0d", dut.u_l1.d_px, channel);
            end
            raw_count[1] = raw_count[1] + 8;
        end
        if (dut.s1_we) begin
            if (dut.s1_px*128 + dut.s1_ocg*8 != act_count[1] ||
                dut.s1_data !== act1[dut.s1_px][dut.s1_ocg*64+:64])
                $fatal(1, "Layer 1 activation or order");
            act_count[1] = act_count[1] + 8;
            if (dut.s1_ocg == 15) committed1 = committed1 + 1;
        end

        if (dut.u_l2.d_run && dut.u_l2.d_glast) begin
            if (dut.u_l2.d_px*128 + dut.u_l2.d_ocg*32 != raw_count[2])
                $fatal(1, "Layer 2 raw order");
            for (channel = 0; channel < 32; channel = channel + 1) begin
                if (dut.u_l2.acc_final[channel] !==
                    raw2[dut.u_l2.d_px][(dut.u_l2.d_ocg*32+channel)*32+:32])
                    $fatal(1, "Layer 2 raw sum, voxel %0d channel %0d", dut.u_l2.d_px, channel);
            end
            raw_count[2] = raw_count[2] + 32;
        end
        if (dut.s2_we) begin
            if (dut.s2_px*128 + dut.s2_ocg*32 != act_count[2] ||
                dut.s2_data !== act2[dut.s2_px][dut.s2_ocg*256+:256])
                $fatal(1, "Layer 2 activation or order");
            act_count[2] = act_count[2] + 32;
            if (dut.s2_ocg == 3) committed2 = committed2 + 1;
        end

        if (dut.u_l3.d_run && dut.u_l3.d_glast) begin
            if (dut.u_l3.d_px*128 + dut.u_l3.d_ocg*32 != raw_count[3])
                $fatal(1, "Layer 3 raw order");
            for (channel = 0; channel < 32; channel = channel + 1) begin
                if (dut.u_l3.acc_final[channel] !==
                    raw3[dut.u_l3.d_px][(dut.u_l3.d_ocg*32+channel)*32+:32])
                    $fatal(1, "Layer 3 raw sum, voxel %0d channel %0d", dut.u_l3.d_px, channel);
            end
            raw_count[3] = raw_count[3] + 32;
        end
        if (dut.s3_we) begin
            if (dut.s3_px*128 + dut.s3_ocg*32 != act_count[3] ||
                dut.s3_data !== act3[dut.s3_px][dut.s3_ocg*256+:256])
                $fatal(1, "Layer 3 activation or order");
            act_count[3] = act_count[3] + 32;
            if (dut.s3_ocg == 3) committed3 = committed3 + 1;
        end

        if (dut.u_l4.d_run && dut.u_l4.d_glast) begin
            if (dut.u_l4.d_px*12 + dut.u_l4.d_ocg*12 != raw_count[4])
                $fatal(1, "Layer 4 raw order");
            for (channel = 0; channel < 12; channel = channel + 1) begin
                if (dut.u_l4.acc_final[channel] !==
                    raw4[dut.u_l4.d_px][(dut.u_l4.d_ocg*12+channel)*32+:32])
                    $fatal(1, "Layer 4 raw sum, voxel %0d channel %0d", dut.u_l4.d_px, channel);
            end
            raw_count[4] = raw_count[4] + 12;
        end
        if (dut.s4_we) begin
            if (dut.s4_px*12 != act_count[4] || dut.s4_dec !== act4[dut.s4_px])
                $fatal(1, "Output prediction or order");
            act_count[4] = act_count[4] + 12;
        end
    end

    initial begin
        $readmemh("input.hex", inputs);
        $readmemh("layer1_raw.hex", raw1);
        $readmemh("layer1_activation.hex", act1);
        $readmemh("layer2_raw.hex", raw2);
        $readmemh("layer2_activation.hex", act2);
        $readmemh("layer3_raw.hex", raw3);
        $readmemh("layer3_activation.hex", act3);
        $readmemh("layer4_raw.hex", raw4);
        $readmemh("layer4_activation.hex", act4);
        for (i = 1; i <= 4; i = i + 1) begin
            raw_count[i] = 0; act_count[i] = 0;
        end
        repeat (5) @(negedge clk);
        rst = 0;
        for (i = 0; i < N; i = i + 1) begin
            @(negedge clk);
            syn_we = 1; syn_addr = i; syn_word = inputs[i];
        end
        @(negedge clk); syn_we = 0; start = 1;
        @(negedge clk); start = 0; checking = 1;
        wait (done === 1'b1);
        @(negedge clk); checking = 0;
        for (i = 1; i <= 4; i = i + 1) begin
            if (raw_count[i] != N*((i==4)?12:128) ||
                act_count[i] != N*((i==4)?12:128))
                $fatal(1, "Incomplete layer %0d output coverage", i);
        end
        if (latency_cycles != 223603) $fatal(1, "Unexpected core cycle count");
        for (i = 0; i < N; i = i + 1) begin
            dec_raddr = i;
            @(negedge clk);
            if (dec_q !== act4[i]) $fatal(1, "Readback voxel %0d", i);
        end
        $display("SMOKE_PASS 3d d=5 tco=32 cycles=%0d raw_values=38016 activations_and_decisions=38016", latency_cycles);
        $finish;
    end
    initial begin
        repeat (500000) @(posedge clk);
        $fatal(1, "Simulation watchdog");
    end
endmodule
