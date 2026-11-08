`timescale 1ns/1ps
module tb_cleanup;
    reg clk = 0;
    always #5 clk = ~clk;
    reg reset = 1, valid_in = 0, use_hdrg = 0;
    reg [5:0] z_checks = 0, x_checks = 0;
    wire valid_out;
    wire [12:0] x_correction, z_correction;
    cleanup_d3 dut (.*);

    integer log_file, received = 0;
    reg previous_valid = 0;
    reg [12:0] previous_record = 0;
    // Check the exact output edge, then save the reply for software comparison.
    task drive(input bit rst, input bit accept, input logic [12:0] record);
        begin
            @(negedge clk);
            reset = rst;
            valid_in = accept;
            {use_hdrg, z_checks, x_checks} = record;
            @(posedge clk);
            #1;
            if (valid_out !== (previous_valid && !rst))
                $fatal(1, "Unexpected output-valid cycle");
            if (valid_out) begin
                if ((^{x_correction, z_correction}) === 1'bx)
                    $fatal(1, "Unknown bit in correction");
                $fdisplay(log_file, "%0d,%0d,%0d", previous_record,
                          x_correction, z_correction);
                received = received + 1;
            end
            previous_valid = accept && !rst;
            previous_record = record;
        end
    endtask

    integer i;
    initial begin
        log_file = $fopen("responses.csv", "w");
        if (!log_file) $fatal(1, "Cannot open output");
        drive(1, 0, 0);
        drive(0, 1, 13'h1fff);
        drive(1, 1, 0);  // Reset discards the pending record.
        for (i = 0; i < 8192; i = i + 1) begin
            if (i % 257 == 0) drive(0, 0, 0);
            // An odd stride visits all inputs and interleaves both modes.
            drive(0, 1, (4099*i) % 8192);
        end
        drive(0, 0, 0);
        drive(0, 0, 0);
        if (received != 8192) $fatal(1, "Missing output records");
        $fclose(log_file);
        $display("PASS: 8192 records, one cycle from acceptance to completion");
        $finish;
    end
endmodule
