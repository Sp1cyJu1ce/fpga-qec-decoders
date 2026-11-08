// Spatial HDRG with a shared cluster walker per CSS component.
// The wide buses are internal patch interfaces, one bit per check/data site
// in the row order used by qec.spatial.Geometry. No physical I/O is implied.
module hdrg_stream #(parameter integer D = 3) (
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
    hdrg_walker #(.D(D), .Z_MASK(0)) x_path
        (clk, reset, accept, z_checks, x_idle, x_correction);
    hdrg_walker #(.D(D), .Z_MASK(1)) z_path
        (clk, reset, accept, x_checks, z_idle, z_correction);
endmodule

module hdrg_walker #(parameter integer D = 3, Z_MASK = 0) (
    input wire clk, reset, start,
    input wire [D*(D-1)-1:0] checks,
    output wire idle,
    output wire [D*D+(D-1)*(D-1)-1:0] correction
);
    localparam ROWS = D-Z_MASK, COLS = D-1+Z_MASK, N = ROWS*COLS;
    localparam W = $clog2(D+1), A = $clog2(N+1), SIDE = 2*D-1;
    localparam IDLE=0, SEED=1, POP=2, EXPLORE=3, RESOLVE=4, MEMBER=5,
               WALK=6, LEVEL=7;
    reg [2:0] state;
    assign idle = state == IDLE;
    reg [W-1:0] seed_row, row_index, stop_row, left_col, right_col, radius;
    reg [W-1:0] root_r, root_c, walk_r, walk_c;
    reg [W-1:0] minimum_left, minimum_right;
    reg [A-1:0] head, tail, member_index;
    reg [COLS-1:0] remaining [0:ROWS-1], deferred [0:ROWS-1];
    reg have_deferred, to_boundary, to_high, defer_cluster;

    // Keep the queue after the breadth-first search. The second pass either
    // moves its members to the next radius or emits their correction paths.
    (* ram_style = "distributed" *) reg [2*W-1:0] queue [0:N-1];
    wire [A-1:0] read_address = state == MEMBER ? member_index : head;
    wire [W-1:0] qr, qc;
    assign {qr,qc} = queue[read_address];
    wire [W-1:0] boundary_axis = Z_MASK ? qr : qc;
    wire [W-1:0] left_distance = boundary_axis + 1'b1;
    wire [W-1:0] right_distance = D-1-boundary_axis;
    wire absorbs = minimum_left <= radius || minimum_right <= radius;

    // Only one row is inspected per cycle. Clip the horizontal search window
    // to the check grid while walking successive rows of the Chebyshev ball.
    wire [W-1:0] selected_row = state == SEED ? seed_row : row_index;
    wire [COLS-1:0] eligible;
    genvar col;
    for (col=0; col<COLS; col=col+1) begin : window
        assign eligible[col] = remaining[selected_row][col] &&
            (state == SEED || (left_col <= col && col <= right_col));
    end
    wire [COLS-1:0] chosen = eligible & (~eligible + 1'b1);
    reg [W-1:0] chosen_col;
    integer i;
    always @* begin
        chosen_col = 0;
        for (i=0; i<COLS; i=i+1)
            chosen_col = chosen_col | (chosen[i] ? W'(i) : {W{1'b0}});
    end
    wire discover = (state == SEED || state == EXPLORE) && |chosen;
    always @(posedge clk) if (discover)
        queue[state == SEED ? 0 : tail] <= {selected_row,chosen_col};

    genvar row;
    for (row=0; row<ROWS; row=row+1) begin : check_rows
        always @(posedge clk) begin
            if (start) begin
                remaining[row] <= checks[row*COLS +: COLS];
                deferred[row] <= 0;
            end else if (state == LEVEL) begin
                remaining[row] <= deferred[row];
                deferred[row] <= 0;
            end else begin
                if (discover && selected_row == row)
                    remaining[row] <= remaining[row] & ~chosen;
                if (state == MEMBER && member_index < tail && defer_cluster && qr == row)
                    deferred[row] <= deferred[row] | ({{(COLS-1){1'b0}},1'b1} << qc);
            end
        end
    end

    // A pair path travels in the row direction first. At a boundary, the
    // last edge terminates outside the check grid and flips the outer data site.
    wire vertical = to_boundary ? Z_MASK != 0 : walk_r != root_r;
    wire forward_step = to_boundary ? to_high : (vertical ? walk_r < root_r : walk_c < root_c);
    wire [W-1:0] walk_axis = Z_MASK ? walk_r : walk_c;
    wire final_boundary_edge = to_boundary && (to_high ? walk_axis == D-2 : walk_axis == 0);
    wire at_root = !to_boundary && walk_r == root_r && walk_c == root_c;
    wire [W:0] full_r = {walk_r,1'b0} + Z_MASK;
    wire [W:0] full_c = {walk_c,1'b0} + (1-Z_MASK);
    wire [W:0] bit_r = !vertical ? full_r : (forward_step ? full_r+1'b1 : full_r-1'b1);
    wire [W:0] bit_c = vertical ? full_c : (forward_step ? full_c+1'b1 : full_c-1'b1);
    genvar data_row, data_col;
    for (data_row=0; data_row<SIDE; data_row=data_row+1) begin : output_row
        for (data_col=data_row%2; data_col<SIDE; data_col=data_col+2) begin : output_site
            reg mask_bit;
            always @(posedge clk) begin
                if (start) mask_bit <= 0;
                else if (state == WALK && !at_root && bit_r == data_row && bit_c == data_col)
                    mask_bit <= !mask_bit;
            end
            assign correction[(data_row*SIDE+data_col)/2] = mask_bit;
        end
    end

    always @(posedge clk) begin
        if (reset) state <= IDLE;
        else case (state)
            IDLE: if (start) begin
                seed_row <= 0;
                have_deferred <= 0;
                radius <= 1;
                state <= SEED;
            end
            SEED: if (|chosen) begin
                root_r <= seed_row;
                root_c <= chosen_col;
                head <= 0;
                tail <= 1;
                minimum_left <= D;
                minimum_right <= D;
                state <= POP;
            end else if (seed_row == ROWS-1) begin
                state <= have_deferred ? LEVEL : IDLE;
            end else seed_row <= seed_row + 1'b1;
            POP: if (head == tail) state <= RESOLVE;
            else begin
                row_index <= qr < radius ? 0 : qr-radius;
                stop_row <= (ROWS-1-qr < radius) ? ROWS-1 : qr+radius;
                left_col <= qc < radius ? 0 : qc-radius;
                right_col <= (COLS-1-qc < radius) ? COLS-1 : qc+radius;
                if (left_distance < minimum_left) minimum_left <= left_distance;
                if (right_distance < minimum_right) minimum_right <= right_distance;
                head <= head + 1'b1;
                state <= EXPLORE;
            end
            EXPLORE: if (|chosen) tail <= tail + 1'b1;
            else if (row_index == stop_row) state <= POP;
            else row_index <= row_index + 1'b1;
            RESOLVE: begin
                to_boundary <= absorbs;
                to_high <= minimum_right < minimum_left;
                defer_cluster <= !absorbs && tail[0];
                if (!absorbs && tail[0]) have_deferred <= 1;
                member_index <= 0;
                state <= MEMBER;
            end
            MEMBER: if (member_index == tail) state <= SEED;
            else begin
                member_index <= member_index + 1'b1;
                if (!defer_cluster) begin
                    walk_r <= qr;
                    walk_c <= qc;
                    state <= WALK;
                end
            end
            WALK: if (at_root || final_boundary_edge) state <= MEMBER;
            else if (vertical) walk_r <= forward_step ? walk_r+1'b1 : walk_r-1'b1;
            else walk_c <= forward_step ? walk_c+1'b1 : walk_c-1'b1;
            LEVEL: begin
                seed_row <= 0;
                have_deferred <= 0;
                radius <= radius << 1;
                state <= SEED;
            end
            default: state <= IDLE;
        endcase
    end
endmodule
