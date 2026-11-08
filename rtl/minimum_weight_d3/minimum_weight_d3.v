// Exact minimum-Hamming-weight correction on the unrotated distance-three patch.
// Both components use the same six-check geometry after transposing the Z path.
module minimum_weight_d3 (
    input  wire        clk,
    input  wire        reset,
    input  wire        valid_in,
    output wire        ready_in,
    input  wire [5:0]  z_checks,
    input  wire [5:0]  x_checks,
    output reg         valid_out,
    output wire [12:0] x_correction,
    output wire [12:0] z_correction
);
    reg issuing;
    reg [3:0] batch;
    reg [5:0] x_syndrome, z_syndrome;
    reg [4:0] pipeline_valid, pipeline_first, pipeline_last;
    wire [12:0] transposed_mask;

    // The last batch uses the old input at the same edge that accepts a new one.
    assign ready_in = !reset && (!issuing || batch == 15);
    always @(posedge clk) begin
        if (reset) begin
            issuing <= 0;
            batch <= 0;
            pipeline_valid <= 0;
            pipeline_first <= 0;
            pipeline_last <= 0;
            valid_out <= 0;
        end else begin
            if (ready_in && valid_in) begin
                x_syndrome <= z_checks;
                z_syndrome <= {x_checks[5], x_checks[2], x_checks[4],
                               x_checks[1], x_checks[3], x_checks[0]};
                issuing <= 1;
                batch <= 0;
            end else if (issuing) begin
                batch <= batch + 1'b1;
                issuing <= (batch != 15);
            end
            pipeline_valid <= {pipeline_valid[3:0], issuing};
            pipeline_first <= {pipeline_first[3:0], batch == 0};
            pipeline_last  <= {pipeline_last[3:0], batch == 15};
            valid_out <= pipeline_valid[4] && pipeline_last[4];
        end
    end

    minimum_weight_d3_component x_path(clk, x_syndrome, batch, pipeline_valid[4],
        pipeline_first[4], pipeline_last[4], x_correction);
    minimum_weight_d3_component z_path(clk, z_syndrome, batch, pipeline_valid[4],
        pipeline_first[4], pipeline_last[4], transposed_mask);

    // Restore row order on the original 5 by 5 checkerboard of data and checks.
    assign z_correction = {transposed_mask[12], transposed_mask[7], transposed_mask[2],
                           transposed_mask[9], transposed_mask[4], transposed_mask[11],
                           transposed_mask[6], transposed_mask[1], transposed_mask[8],
                           transposed_mask[3], transposed_mask[10], transposed_mask[5],
                           transposed_mask[0]};
endmodule


module minimum_weight_d3_component (
    input  wire        clk,
    input  wire [5:0]  syndrome,
    input  wire [3:0]  batch,
    input  wire        batch_valid,
    input  wire        first_batch,
    input  wire        last_batch,
    output reg  [12:0] correction
);
    // Number data sites by row. For each pair of checks in an even row, the
    // leftmost and rightmost data bits each touch just one of those checks.
    // Choosing the other seven bits fixes these six boundary bits by parity.
    function [12:0] complete_mask(input [5:0] checks, input [6:0] free_bits);
        reg [12:0] m;
        begin
            {m[11], m[9], m[8], m[6], m[4], m[3], m[1]} = free_bits;
            m[0]  = checks[0] ^ m[1]  ^ m[3];
            m[2]  = checks[1] ^ m[1]  ^ m[4];
            m[5]  = checks[2] ^ m[6]  ^ m[3] ^ m[8];
            m[7]  = checks[3] ^ m[6]  ^ m[4] ^ m[9];
            m[10] = checks[4] ^ m[11] ^ m[8];
            m[12] = checks[5] ^ m[11] ^ m[9];
            complete_mask = m;
        end
    endfunction

    function [3:0] weight(input [12:0] mask);
        integer i;
        begin
            weight = 0;
            for (i = 0; i < 13; i = i + 1) weight = weight + mask[i];
        end
    endfunction

    // Keep the earlier seven-bit assignment when costs tie. This applies both
    // within a group of eight and when updating the best answer across groups.
    function [16:0] lighter(input [16:0] earlier, input [16:0] later);
        lighter = (earlier[16:13] <= later[16:13]) ? earlier : later;
    endfunction

    reg [16:0] scored [0:7], pairs [0:3], halves [0:1];
    reg [16:0] group_best, best_so_far;
    wire [16:0] best = first_batch ? group_best : lighter(best_so_far, group_best);
    genvar lane;
    generate
        for (lane = 0; lane < 8; lane = lane + 1) begin : candidates
            localparam [2:0] lane_number = lane;
            reg [12:0] mask;
            always @(posedge clk) begin
                mask <= complete_mask(syndrome, {batch, lane_number});
                scored[lane] <= {weight(mask), mask};
            end
        end
    endgenerate

    integer i;
    always @(posedge clk) begin
        for (i = 0; i < 4; i = i + 1)
            pairs[i] <= lighter(scored[2*i], scored[2*i+1]);
        for (i = 0; i < 2; i = i + 1)
            halves[i] <= lighter(pairs[2*i], pairs[2*i+1]);
        group_best <= lighter(halves[0], halves[1]);
        if (batch_valid) begin
            best_so_far <= best;
            if (last_batch) correction <= best[12:0];
        end
    end
endmodule
