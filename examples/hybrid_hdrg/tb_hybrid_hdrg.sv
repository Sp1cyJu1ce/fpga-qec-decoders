`timescale 1ns/1ps
module tb_hybrid_hdrg #(parameter integer D = 5);
    localparam W = 2*D-1, N = D*(D-1), Q = D*D+(D-1)*(D-1);
    localparam IN_WORDS = (W+10)*(W+10);
    reg clk = 0, rst = 1, start = 0, syn_we = 0;
    reg [15:0] syn_addr = 0;
    reg [3:0] syn_nib = 0;
    wire busy, done;
    wire [Q-1:0] x_correction, z_correction;
    cnn_hdrg #(.D(D)) dut(.*);
    always #5 clk = ~clk;
    integer tick = 0;
    always @(posedge clk) tick <= tick+1;

    reg [Q-1:0] lx, lz, fx, fz;
    reg [N-1:0] rz, rx;
    integer count, frame, pixel, accepted, predicted, cleanup_start, cleanup_finish;
    integer inputs, reference;
    initial begin
        inputs = $fopen("cnn_inputs.hex","r");
        reference = $fopen("expected.hex","r");
        if (!inputs || !reference || $fscanf(reference,"%d",count) != 1 || count <= 0)
            $fatal(1,"Invalid test vectors");
        repeat (5) @(negedge clk);
        rst = 0;
        for (frame = 0; frame < count; frame = frame+1) begin
            if ($fscanf(reference,"%h %h %h %h %h %h",lx,lz,rz,rx,fx,fz) != 6)
                $fatal(1,"Missing expected record");
            for (pixel = 0; pixel < IN_WORDS; pixel = pixel+1) begin
                if ($fscanf(inputs,"%h",syn_nib) != 1) $fatal(1,"Incomplete CNN input");
                syn_we = 1; syn_addr = pixel;
                @(negedge clk);
            end
            syn_we = 0; start = 1;
            @(negedge clk); accepted = tick; start = 0;
            while (dut.cnn_done !== 1) @(negedge clk);
            predicted = tick;
            // Once ISSUE is visible at a falling edge, the engine accepts the
            // residual on the next rising edge. Both output masks are checked.
            while (dut.state !== dut.ISSUE || dut.cleanup_ready !== 1) @(negedge clk);
            if ({dut.local_x,dut.local_z,dut.residual_z,dut.residual_x} !== {lx,lz,rz,rx})
                $fatal(1,"CNN or residual mismatch on frame %0d",frame);
            @(negedge clk); cleanup_start = tick;
            while (dut.cleanup_done !== 1) @(negedge clk);
            cleanup_finish = tick;
            @(negedge clk);
            if (done !== 1 || busy !== 0 || {x_correction,z_correction} !== {fx,fz})
                $fatal(1,"Combined mask mismatch on frame %0d",frame);
            if (predicted-accepted != dut.cnn.latency_cycles || cleanup_start-predicted != Q+2)
                $fatal(1,"Unexpected CNN handoff timing");
            $display("HYBRID %0d %0d %0d %0d %0d %0d %0d",D,frame,predicted-accepted,
                     cleanup_start-predicted,cleanup_finish-cleanup_start,tick-predicted,tick-accepted);
        end
        $display("HYBRID_PASS %0d",count);
        $finish;
    end
endmodule
