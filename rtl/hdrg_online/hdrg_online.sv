// HDRG for one unrotated, perfect-check patch. Both CSS components grow
// clusters at the same time. The two bit orders follow qec.spatial.Geometry.
// Tested patch distances: 3 and 5. The input is accepted when ready_in and
// valid_in are high at a rising edge. Both masks are meaningful at valid_out.
module hdrg_online #(parameter integer D = 3) (
    input wire clk, reset, valid_in,
    input wire [D*(D-1)-1:0] z_checks, x_checks,
    output wire ready_in, valid_out,
    output wire [D*D+(D-1)*(D-1)-1:0] x_correction, z_correction
);
    wire x_idle, z_idle;
    reg pending;
    assign ready_in = x_idle && z_idle && !reset;
    assign valid_out = pending && ready_in;
    wire accept = valid_in && ready_in;

    always @(posedge clk) begin
        if (reset) pending <= 0;
        else if (ready_in) pending <= valid_in;
    end
    hdrg_component #(.D(D), .Z_MASK(0)) x_engine (
        .clk(clk), .reset(reset), .start(accept), .checks(z_checks),
        .idle(x_idle), .correction(x_correction)
    );
    hdrg_component #(.D(D), .Z_MASK(1)) z_engine (
        .clk(clk), .reset(reset), .start(accept), .checks(x_checks),
        .idle(z_idle), .correction(z_correction)
    );
endmodule

module hdrg_component #(parameter integer D = 3, Z_MASK = 0) (
    input wire clk, reset, start,
    input wire [D*(D-1)-1:0] checks,
    output wire idle,
    output reg [D*D+(D-1)*(D-1)-1:0] correction
);
    localparam N = D*(D-1), Q = D*D+(D-1)*(D-1), WIDTH = 2*D-1;
    localparam COLS = Z_MASK ? D : D-1;
    localparam IDLE = 0, SEED = 1, GROW = 2, RESOLVE = 3;
    reg [1:0] state;
    reg [$clog2(2*D)-1:0] radius;
    reg [N-1:0] remaining, deferred, cluster, root;
    wire [N-1:0] grown;
    assign idle = state == IDLE;

    function automatic integer separation(input integer a, b);
        integer dr, dc;
        begin
            dr = (a/COLS) - (b/COLS);
            dc = (a%COLS) - (b%COLS);
            if (dr < 0) dr = -dr;
            if (dc < 0) dc = -dc;
            separation = dr > dc ? dr : dc;
        end
    endfunction

    // Every pass adds the remaining defects that touch the current cluster.
    // Including the starting site keeps every previously reached member.
    genvar node, neighbour;
    for (node=0; node<N; node=node+1) begin : flood_node
        wire [N-1:0] touches;
        for (neighbour=0; neighbour<N; neighbour=neighbour+1) begin : neighbour_test
            localparam STEPS = separation(node, neighbour);
            assign touches[neighbour] = cluster[neighbour] && radius >= STEPS;
        end
        assign grown[node] = remaining[node] && |touches;
    end

    // Integer coordinates below refer to the full (2d-1) x (2d-1) lattice.
    // A return path first moves in the row direction, then along the column.
    // These elaboration-time masks describe paths between sites. At run time
    // the cluster membership determines which of those paths cancel in XOR.
    function automatic integer between(input integer v, a, b);
        between = (a < v && v < b) || (b < v && v < a);
    endfunction

    function automatic [N-1:0] path_sources(input integer bit_id, anchor, side);
        integer r, c, dest_r, dest_c, src, src_r, src_c;
        begin
            r = 2*bit_id / WIDTH;
            c = 2*bit_id % WIDTH;
            dest_r = 2*(anchor/COLS) + Z_MASK;
            dest_c = 2*(anchor%COLS) + (1-Z_MASK);
            path_sources = 0;
            for (src=0; src<N; src=src+1) begin
                src_r = 2*(src/COLS) + Z_MASK;
                src_c = 2*(src%COLS) + (1-Z_MASK);
                if (side == 2)
                    path_sources[src] = (c == src_c && between(r,src_r,dest_r)) ||
                                        (r == dest_r && between(c,src_c,dest_c));
                else if (Z_MASK)
                    path_sources[src] = c == src_c && (side ? r > src_r : r < src_r);
                else
                    path_sources[src] = r == src_r && (side ? c > src_c : c < src_c);
            end
        end
    endfunction

    wire [Q-1:0] to_low, to_high, to_root;
    genvar data_bit, anchor;
    for (data_bit=0; data_bit<Q; data_bit=data_bit+1) begin : mask_bit
        localparam [N-1:0] LOW_PATHS = path_sources(data_bit, 0, 0);
        localparam [N-1:0] HIGH_PATHS = path_sources(data_bit, 0, 1);
        assign to_low[data_bit] = ^(cluster & LOW_PATHS);
        assign to_high[data_bit] = ^(cluster & HIGH_PATHS);
        wire [N-1:0] root_paths;
        for (anchor=0; anchor<N; anchor=anchor+1) begin : destination
            localparam [N-1:0] PATHS = path_sources(data_bit, anchor, 2);
            assign root_paths[anchor] = root[anchor] && ^(cluster & PATHS);
        end
        assign to_root[data_bit] = |root_paths;
    end

    function automatic [N-1:0] band(input integer offset);
        integer src;
        begin
            for (src=0; src<N; src=src+1)
                band[src] = (Z_MASK ? src/COLS : src%COLS) == offset;
        end
    endfunction

    wire [D-2:0] near_low, near_high;
    genvar stripe;
    for (stripe=0; stripe<D-1; stripe=stripe+1) begin : edge_distance
        localparam [N-1:0] LOW = band(stripe), HIGH = band(D-2-stripe);
        assign near_low[stripe] = |(cluster & LOW);
        assign near_high[stripe] = |(cluster & HIGH);
    end
    integer depth;
    reg on_boundary, high_side;
    always @* begin
        on_boundary = 0;
        high_side = 0;
        // Nearer boundaries take precedence. An equal-distance tie picks
        // the left edge for X correction or the top edge for Z correction.
        for (depth=D-1; depth>=1; depth=depth-1) begin
            if (radius >= depth && (near_low[depth-1] || near_high[depth-1])) begin
                on_boundary = 1;
                high_side = !near_low[depth-1];
            end
        end
    end

    wire [N-1:0] first = remaining & (~remaining + 1'b1);
    always @(posedge clk) begin
        if (reset) begin
            state <= IDLE;
            correction <= 0;
        end else case (state)
            IDLE: if (start) begin
                correction <= 0;
                remaining <= checks;
                deferred <= 0;
                radius <= 1;
                state <= SEED;
            end
            SEED: begin
                if (remaining != 0) begin
                    root <= first;
                    cluster <= first;
                    state <= GROW;
                end else if (deferred != 0) begin
                    remaining <= deferred;
                    deferred <= 0;
                    radius <= radius << 1;
                end else state <= IDLE;
            end
            GROW: begin
                cluster <= grown;
                if (grown == cluster) state <= RESOLVE;
            end
            RESOLVE: begin
                remaining <= remaining & ~cluster;
                if (on_boundary)
                    correction <= correction ^ (high_side ? to_high : to_low);
                else if (!(^cluster))
                    correction <= correction ^ to_root;
                else
                    deferred <= deferred | cluster;
                state <= SEED;
            end
        endcase
    end
endmodule
