// A three-lane resident worker supplies each local correction when it is written.
// To compare with the banked Arty core, both accept the same padded raster input.
module resident_hdrg #(parameter integer D = 5) (
    input wire clk, rst, start,
    input wire syn_we,
    input wire [15:0] syn_addr,
    input wire [3:0] syn_nib,
    output wire busy,
    output reg done,
    output reg [D*D+(D-1)*(D-1)-1:0] x_correction, z_correction
);
    localparam W = 2*D-1, FRAME = W+10;
    localparam N = D*(D-1), Q = D*D+(D-1)*(D-1);
    localparam IDLE = 0, CNN = 1, ISSUE = 2, RETURN = 3;
    reg [2:0] state;
    reg [N-1:0] x_checks, z_checks;
    reg [Q-1:0] local_x, local_z;
    reg nonempty;
    wire load = syn_we && state == IDLE && !start && !rst;
    wire cnn_done;
    localparam V = 3;
    localparam CW = $clog2(W);
    wire [V-1:0] result_valid;
    wire [V*CW-1:0] result_x, result_y;
    wire [V*2-1:0] result_data;
    assign busy = state != IDLE;

    // The resident worker generates zero padding internally. Strip the external
    // five-site border used by the existing test frames and write one lane.
    wire [15:0] padded_x = syn_addr % FRAME;
    wire [15:0] padded_y = syn_addr / FRAME;
    wire inside_map = padded_x >= 5 && padded_x < W+5 && padded_y >= 5 && padded_y < W+5;
    wire [CW-1:0] load_x = padded_x - 5, load_y = padded_y - 5;
    worker_rowready #(.N(W), .V(V)) cnn (
        .clk(clk), .reset(rst), .start(start && state == IDLE), .frame_slot(1'b0),
        .issue_enable(3'b111), .load_ready(), .busy(), .done(cnn_done), .elapsed_cycles(),
        .load_valid({{(V-1){1'b0}}, load && inside_map}),
        .load_x({{((V-1)*CW){1'b0}}, load_x}), .load_y({{((V-1)*CW){1'b0}}, load_y}),
        .load_data({{((V-1)*4){1'b0}}, syn_nib}),
        .result_valid(result_valid), .result_x(result_x), .result_y(result_y), .result_data(result_data),
        .result_read_enable(1'b0), .result_read_slot(1'b0), .result_read_x({CW{1'b0}}),
        .result_read_y({CW{1'b0}}), .result_read_valid(), .result_read_data()
    );

    // A valid lane names a site on the W by W map. Even raster addresses
    // are data qubits; odd addresses are checks. Each data site is visited once.
    // Results from different lanes in a clock therefore write distinct bits.
    always @(posedge clk) begin
        for (integer lane=0; lane<V; lane=lane+1) begin
            integer address;
            address = result_y[lane*CW +: CW]*W + result_x[lane*CW +: CW];
            if (state == CNN && result_valid[lane] && !address[0]) begin
                local_x[address/2] <= nonempty && result_data[2*lane];
                local_z[address/2] <= nonempty && result_data[2*lane+1];
            end
        end
    end

    // k names a check in the same raster order as qec.spatial.Geometry.
    // Its four neighbouring data bits are fixed wires. At a boundary the
    // missing neighbour contributes zero, so no run-time matrix is needed.
    wire [N-1:0] residual_z, residual_x;
    genvar k;
    for (k = 0; k < N; k = k+1) begin : parity
        localparam ZR = 2*(k/(D-1)), ZC = 2*(k%(D-1))+1;
        localparam XR = 2*(k/D)+1, XC = 2*(k%D);
        localparam ZP = ZR*W+ZC, XP = XR*W+XC;
        always @(posedge clk) begin
            if (load && syn_addr == (5+ZR)*FRAME+5+ZC) z_checks[k] <= syn_nib[1];
            if (load && syn_addr == (5+XR)*FRAME+5+XC) x_checks[k] <= syn_nib[0];
        end
        wire above, below, left_bit, right_bit;
        if (ZR > 0) assign above = local_x[(ZP-W)/2]; else assign above = 0;
        if (ZR < W-1) assign below = local_x[(ZP+W)/2]; else assign below = 0;
        if (XC > 0) assign left_bit = local_z[(XP-1)/2]; else assign left_bit = 0;
        if (XC < W-1) assign right_bit = local_z[(XP+1)/2]; else assign right_bit = 0;
        assign residual_z[k] = z_checks[k] ^ above ^ below ^ local_x[(ZP-1)/2] ^ local_x[(ZP+1)/2];
        assign residual_x[k] = x_checks[k] ^ left_bit ^ right_bit ^ local_z[(XP-W)/2] ^ local_z[(XP+W)/2];
    end

    wire cleanup_ready, cleanup_done;
    wire [Q-1:0] global_x, global_z;
    hdrg_stream #(.D(D)) cleanup (
        .clk(clk), .reset(rst), .valid_in(state == ISSUE), .ready_in(cleanup_ready),
        .z_checks(residual_z), .x_checks(residual_x), .valid_out(cleanup_done),
        .x_correction(global_x), .z_correction(global_z)
    );

    always @(posedge clk) begin
        done <= 0;
        if (rst) state <= IDLE;
        else case (state)
            IDLE: if (start) begin
                nonempty <= |{x_checks,z_checks};
                state <= CNN;
            end
            CNN: if (cnn_done) state <= ISSUE;
            ISSUE: if (cleanup_ready) state <= RETURN;
            RETURN: if (cleanup_done) begin
                x_correction <= local_x ^ global_x;
                z_correction <= local_z ^ global_z;
                done <= 1;
                state <= IDLE;
            end
            default: state <= IDLE;
        endcase
    end
endmodule
