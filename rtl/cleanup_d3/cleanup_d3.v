// Whole-patch cleanup for the distance-three, perfect-check 2D experiment.
// Syndrome bit order and correction bit order follow qec.spatial.Geometry.
module cleanup_d3 #(
    parameter X_FILE = "x_corrections.hex",
    parameter Z_FILE = "z_corrections.hex"
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        valid_in,
    input  wire        use_hdrg,
    input  wire [5:0]  z_checks,
    input  wire [5:0]  x_checks,
    output reg         valid_out,
    output reg  [12:0] x_correction,
    output reg  [12:0] z_correction
);
    // The upper address bit selects MWPM (0) or HDRG (1).
    reg [6:0] x_address, z_address;
    reg       pending;
    reg [12:0] x_table [0:127];
    reg [12:0] z_table [0:127];
    initial begin
        $readmemh(X_FILE, x_table);
        $readmemh(Z_FILE, z_table);
    end

    // A record accepted on one rising edge is returned on the next.
    // Consecutive records may use different algorithms.
    always @(posedge clk) begin
        if (reset) begin
            pending <= 1'b0;
            valid_out <= 1'b0;
        end else begin
            pending <= valid_in;
            valid_out <= pending;
            if (valid_in) begin
                x_address <= {use_hdrg, z_checks};
                z_address <= {use_hdrg, x_checks};
            end
            if (pending) begin
                x_correction <= x_table[x_address];
                z_correction <= z_table[z_address];
            end
        end
    end
endmodule
