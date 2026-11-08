`timescale 1ns/1ps
module tb_hybrid;
    reg clk = 0, rst = 1, start = 0, use_hdrg = 0, syn_we = 0;
    reg [15:0] syn_addr = 0;
    reg [3:0] syn_nib = 0;
    wire busy, done;
    wire [12:0] x_correction, z_correction;
    cnn_cleanup_d3 dut(.*);
    always #5 clk = ~clk;
    integer tick = 0;
    always @(posedge clk) tick <= tick + 1;

    reg [3:0] frames [0:64*225-1];
    reg [52:0] expected [0:63];
    integer count, f, pixel, accepted, predicted, frame_file;
    initial begin
        frame_file = $fopen("frames.txt", "r");
        if (frame_file == 0 || $fscanf(frame_file, "%d", count) != 1)
            $fatal(1, "Missing frame count");
        $fclose(frame_file);
        if (count < 2 || count > 64) $fatal(1, "Invalid frame count");
        $readmemh("inputs.hex", frames, 0, count*225-1);
        $readmemh("expected.hex", expected, 0, count-1);
        repeat (4) @(negedge clk);
        rst = 0;
        for (f = 0; f < count; f = f+1) begin
            for (pixel = 0; pixel < 225; pixel = pixel+1) begin
                syn_we = 1;
                syn_addr = pixel;
                syn_nib = frames[225*f+pixel];
                @(negedge clk);
            end
            syn_we = 0; start = 1; use_hdrg = expected[f][52];
            @(negedge clk);
            accepted = tick;
            if (busy !== 1 || done !== 0) $fatal(1, "Start failed on frame %0d", f);
            start = 0; use_hdrg = !use_hdrg;
            @(negedge clk);
            // A caller cannot replace the frame until both corrections return.
            start = 1; syn_we = 1; syn_addr = 81; syn_nib = ~frames[225*f+81];
            @(negedge clk); start = 0; syn_we = 0;
            while (dut.cnn_done !== 1) @(negedge clk);
            predicted = tick;
            while (done !== 1) @(negedge clk);
            if ({dut.local_x, dut.local_z, x_correction, z_correction} !== expected[f][51:0])
                $fatal(1, "Wrong prediction or combined correction on frame %0d", f);
            if (busy !== 0 || tick-predicted != 17 || predicted-accepted != dut.cnn.latency_cycles)
                $fatal(1, "Wrong completion time on frame %0d", f);
            $display("HYBRID_FRAME frame=%0d cnn_cycles=%0d added_cycles=%0d total_cycles=%0d",
                     f, predicted-accepted, tick-predicted, tick-accepted);
        end
        $display("HYBRID_PASS frames=%0d", count);
        $finish;
    end
    initial begin
        repeat (64*6000) @(posedge clk);
        $fatal(1, "Incomplete CNN / cleanup transaction");
    end
endmodule
