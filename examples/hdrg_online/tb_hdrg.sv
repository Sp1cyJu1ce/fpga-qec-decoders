`timescale 1ns/1ps
module tb_hdrg;
    parameter integer D = 3;
    localparam N = D*(D-1), Q = D*D+(D-1)*(D-1);
    reg clk = 0;
    always #5 clk = ~clk;
    reg reset = 1, valid_in = 0;
    reg [N-1:0] z_checks = 0, x_checks = 0;
    wire ready_in, valid_out;
    wire [Q-1:0] x_correction, z_correction;
    hdrg_online #(.D(D)) dut (.*);

    integer stream, replies, fields, records = 0, elapsed, expected_cycles;
    reg [Q-1:0] expected_x, expected_z;
    reg [N-1:0] next_z, next_x;
    initial begin
        stream = $fopen("stimulus.txt", "r");
        replies = $fopen("responses.csv", "w");
        if (!stream || !replies) $fatal(1, "Cannot open vector files");
        @(posedge clk); @(negedge clk);
        // Abandon a non-empty request. Reset must withdraw any completion.
        reset = 0; valid_in = 1; z_checks = '1; x_checks = '1;
        @(posedge clk); @(negedge clk);
        reset = 1;
        @(posedge clk); @(negedge clk);
        reset = 0; valid_in = 0;
        #1;
        if (!ready_in || valid_out) $fatal(1, "Bad reset handshake");

        while (!$feof(stream)) begin
            fields = $fscanf(stream, "%h %h %h %h %d", next_z, next_x,
                             expected_x, expected_z, expected_cycles);
            if (fields == 5) begin
                if (!ready_in) $fatal(1, "Ready missing before input");
                z_checks = next_z; x_checks = next_x; valid_in = 1;
                @(posedge clk); #1;
                if (ready_in || valid_out) $fatal(1, "Bad acceptance");
                elapsed = 0;
                while (!valid_out) begin
                    @(negedge clk);
                    // Traffic with ready low must never replace the input.
                    valid_in = 1; z_checks = ~next_z; x_checks = ~next_x;
                    @(posedge clk); #1;
                    elapsed = elapsed + 1;
                    if (ready_in !== valid_out || elapsed > expected_cycles)
                        $fatal(1, "Bad handshake or timeout at vector %0d", records);
                end
                if (elapsed != expected_cycles || x_correction !== expected_x ||
                    z_correction !== expected_z)
                    $fatal(1, "Wrong mask or latency at vector %0d", records);
                $fdisplay(replies, "%h,%h,%h,%h,%0d", next_z, next_x,
                          x_correction, z_correction, elapsed);
                records = records + 1;
                @(negedge clk);
                if (records % 73 == 0) begin
                    valid_in = 0;
                    @(posedge clk); #1;
                    if (valid_out || !ready_in) $fatal(1, "Stale completion");
                    @(negedge clk);
                end
            end else if (!$feof(stream)) $fatal(1, "Malformed stimulus");
        end
        valid_in = 0;
        @(posedge clk); #1;
        if (valid_out || !ready_in) $fatal(1, "Unexpected final response");
        $fclose(stream);
        $fclose(replies);
        $display("PASS: %0d HDRG records", records);
        $finish;
    end
endmodule
