`timescale 1ns/1ps
module tb_minimum_weight_d3;
    localparam LATENCY = 21, INTERVAL = 16;
    reg clk = 0;
    always #5 clk = ~clk;
    reg reset = 1, valid_in = 0;
    reg [5:0] z_checks = 0, x_checks = 0;
    wire ready_in, valid_out;
    wire [12:0] x_correction, z_correction;
    minimum_weight_d3 dut (.*);

    integer output_file, cycle = 0, next_ready = 0, received = 0;
    reg accepted;
    reg [LATENCY-1:0] expected_valid = 0;
    reg [11:0] records [0:LATENCY-1];
    integer accepted_at [0:LATENCY-1];

    task drive(input bit rst, input bit offer, input logic [11:0] record);
        integer k;
        begin
            @(negedge clk);
            reset = rst;
            valid_in = offer;
            {z_checks, x_checks} = record;
            @(posedge clk);
            cycle = cycle + 1;
            if (ready_in !== (!rst && cycle >= next_ready))
                $fatal(1, "Unexpected input-ready at cycle %0d", cycle);
            accepted = valid_in && ready_in;
            #1;
            if (valid_out !== (!rst && expected_valid[LATENCY-1]))
                $fatal(1, "Unexpected output-valid at cycle %0d", cycle);
            if (valid_out) begin
                if ((^{x_correction, z_correction}) === 1'bx)
                    $fatal(1, "Unknown bit in output mask");
                $fdisplay(output_file, "%0d,%0d,%0d,%0d,%0d", records[LATENCY-1],
                          x_correction, z_correction, accepted_at[LATENCY-1], cycle);
                received = received + 1;
            end
            if (rst) begin
                expected_valid = 0;
                next_ready = 0;
            end else begin
                expected_valid = {expected_valid[LATENCY-2:0], accepted};
                for (k = LATENCY-1; k > 0; k = k - 1) begin
                    records[k] = records[k-1];
                    accepted_at[k] = accepted_at[k-1];
                end
                records[0] = record;
                accepted_at[0] = cycle;
                if (accepted) next_ready = cycle + INTERVAL;
            end
        end
    endtask

    integer n, delay_cycles;
    initial begin
        output_file = $fopen("responses.csv", "w");
        if (!output_file) $fatal(1, "Cannot open response log");
        drive(1, 0, 0);
        // Reset once in each pipeline phase, including on a would-be return.
        for (delay_cycles = 0; delay_cycles < LATENCY; delay_cycles = delay_cycles + 1) begin
            drive(0, 1, 12'hfff);
            repeat (delay_cycles) drive(0, 0, 0);
            drive(1, 1, 12'h000);
        end
        drive(0, 1, 12'h123);
        repeat (INTERVAL-1) drive(0, 0, 0);
        drive(0, 1, 12'h456);
        drive(1, 0, 0);  // Both accepted frames are still in flight.
        for (n = 0; n < 4096; n = n + 1) begin
            if (n % 257 == 0) repeat (LATENCY+2) drive(0, 0, 0);
            // This odd stride visits all pairs rather than changing one axis at a time.
            drive(0, 1, 2053*n);
            while (!accepted) drive(0, 1, 2053*n);
        end
        repeat (LATENCY+1) drive(0, 0, 0);
        if (received != 4096) $fatal(1, "Expected 4096 replies, got %0d", received);
        $fclose(output_file);
        $display("PASS: all 4096 records completed in 21 clocks");
        $finish;
    end
endmodule
