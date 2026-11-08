// One frame in the unrotated perfect-check spatial experiment. Load every
// padded CNN input before start. Both correction masks are valid at done.
module cnn_hdrg #(parameter integer D = 5) (
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
    localparam IDLE = 0, CNN = 1, READ = 2, ISSUE = 3, RETURN = 4;
    reg [2:0] state;
    reg [$clog2(Q)-1:0] site;
    reg [N-1:0] x_checks, z_checks;
    reg [Q-1:0] local_x, local_z;
    reg nonempty;
    wire load = syn_we && state == IDLE && !start && !rst;
    wire cnn_done;
    wire [1:0] decision;
    assign busy = state != IDLE;

    // The decision RAM reads a pixel on each rising edge. Data qubits are
    // the even raster addresses. Address 0 is primed while awaiting the CNN.
    wire [15:0] read_addr = state == READ && site < Q-1 ? 2*(site+1) : 0;
    decoder_top_v2 #(.D(D), .P1(11), .P2(4), .P3(6), .DSP_L2(1), .DSP_L3(6)) cnn (
        .clk(clk), .rst(rst), .start(start && state == IDLE), .busy(),
        .done(cnn_done), .latency_cycles(), .syn_we(load), .syn_addr(syn_addr),
        .syn_nib(syn_nib), .dec_raddr(read_addr), .dec_q(decision)
    );

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
            CNN: if (cnn_done) begin
                site <= 0;
                state <= READ;
            end
            READ: begin
                // After Q shifts the first data site's decision is in bit 0.
                local_x <= {nonempty && decision[0], local_x[Q-1:1]};
                local_z <= {nonempty && decision[1], local_z[Q-1:1]};
                if (site == Q-1) state <= ISSUE;
                else site <= site+1'b1;
            end
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
