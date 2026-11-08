`timescale 1ns/1ps
module tb_hdrg_stream #(parameter integer D=3, NF=1);
    localparam N=D*(D-1), Q=D*D+(D-1)*(D-1);
    reg clk=0, reset=1, valid_in=0;
    reg [N-1:0] z_checks, x_checks;
    wire ready_in, valid_out;
    wire [Q-1:0] x_correction, z_correction;
    hdrg_stream #(.D(D)) dut(.*);
    always #5 clk=~clk;

    reg [N-1:0] sz, sx;
    reg [Q-1:0] expected_x, expected_z;
    integer frame, cycles, source, output_file;
    initial begin
        source=$fopen("stimulus.txt","r");
        output_file=$fopen("responses.csv","w");
        if (!source || !output_file) $fatal(1,"Cannot open test records");
        repeat (3) @(negedge clk);
        reset=0;
        // Abort an in-flight record and check that a new frame starts cleanly.
        valid_in=1; z_checks='1; x_checks='1;
        @(negedge clk); valid_in=0;
        repeat (3) @(negedge clk);
        reset=1;
        @(negedge clk); reset=0;
        #1;
        if (valid_out !== 0 || ready_in !== 1) $fatal(1,"Reset left a pending record");
        for (frame=0; frame<NF; frame=frame+1) begin
            if ($fscanf(source,"%h %h %h %h",sz,sx,expected_x,expected_z)!=4)
                $fatal(1,"Missing vector %0d",frame);
            if (ready_in !== 1) $fatal(1,"Cannot start frame %0d",frame);
            z_checks=sz; x_checks=sx; valid_in=1;
            @(negedge clk); valid_in=0;
            // The complete input must have been captured at the acceptance edge.
            z_checks=~sz; x_checks=~sx;
            cycles=0;
            while (valid_out !== 1 && cycles < 1000000) begin
                @(negedge clk); cycles=cycles+1;
            end
            if (valid_out !== 1 || x_correction !== expected_x || z_correction !== expected_z)
                $fatal(1,"Distance %0d frame %0d mismatch or timeout at %0d clocks",D,frame,cycles);
            $fdisplay(output_file,"%0d,%0d",frame,cycles);
        end
        $fclose(output_file); $fclose(source);
        $display("WALKER_PASS d=%0d frames=%0d",D,NF);
        $finish;
    end
endmodule
