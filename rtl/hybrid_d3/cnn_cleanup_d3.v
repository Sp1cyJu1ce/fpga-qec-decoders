// One integer CNN pass followed by whole-patch lookup cleanup. Load the
// padded 15 x 15 frame through the original CNN input port before start.
// done pulses when both combined correction masks are available.
`timescale 1ns/1ps
module cnn_cleanup_d3 (
    input wire clk, rst, start, use_hdrg,
    input wire syn_we,
    input wire [15:0] syn_addr,
    input wire [3:0] syn_nib,
    output wire busy,
    output reg done,
    output reg [12:0] x_correction, z_correction
);
    localparam IDLE = 0, CNN = 1, READ = 2, ISSUE = 3, RETURN = 4;
    reg [2:0] state;
    reg mode;
    wire load = syn_we && state == IDLE && !start && !rst;
    reg [5:0] z_checks, x_checks;
    reg [12:0] local_x, local_z;
    reg [3:0] site;
    assign busy = state != IDLE;

    // Each check sees its nearest data neighbours on the 5 x 5 grid.
    // Data sites occupy the even raster addresses, including row transitions.
    function [12:0] neighbours;
        input integer row, col;
        integer pixel, r, c;
        begin
            neighbours = 0;
            for (pixel = 0; pixel < 25; pixel = pixel + 2) begin
                r = pixel / 5;
                c = pixel % 5;
                if ((r == row && (c+1 == col || c == col+1)) ||
                    (c == col && (r+1 == row || r == row+1)))
                    neighbours[pixel/2] = 1'b1;
            end
        end
    endfunction

    wire [5:0] residual_z, residual_x;
    genvar k;
    generate for (k = 0; k < 6; k = k + 1) begin : parity
        localparam ZR = 2*(k/2), ZC = 2*(k%2)+1;
        localparam XR = 2*(k/3)+1, XC = 2*(k%3);
        // Keep the original checks while the CNN input memory is loaded.
        always @(posedge clk) begin
            if (load && syn_addr == (5+ZR)*15+5+ZC) z_checks[k] <= syn_nib[1];
            if (load && syn_addr == (5+XR)*15+5+XC) x_checks[k] <= syn_nib[0];
        end
        assign residual_z[k] = z_checks[k] ^ (^(local_x & neighbours(ZR,ZC)));
        assign residual_x[k] = x_checks[k] ^ (^(local_z & neighbours(XR,XC)));
    end endgenerate

    wire cnn_done;
    wire [1:0] decision;
    // While capturing one decision, present the next data address to the
    // synchronous output RAM. Address zero is already selected on entry.
    wire [15:0] read_addr = state == READ && site < 12 ? 2*(site+1) : 0;
    decoder_top_v2 #(.D(3), .P1(11), .P2(4), .P3(6), .DSP_L2(1), .DSP_L3(6)) cnn (
        .clk(clk), .rst(rst), .start(start && state == IDLE), .busy(),
        .done(cnn_done), .latency_cycles(), .syn_we(load), .syn_addr(syn_addr),
        .syn_nib(syn_nib), .dec_raddr(read_addr), .dec_q(decision)
    );

    wire cleanup_done;
    wire [12:0] global_x, global_z;
    cleanup_d3 global_stage (
        .clk(clk), .reset(rst), .valid_in(state == ISSUE), .use_hdrg(mode),
        .z_checks(residual_z), .x_checks(residual_x), .valid_out(cleanup_done),
        .x_correction(global_x), .z_correction(global_z)
    );

    always @(posedge clk) begin
        done <= 0;
        if (rst) begin
            state <= IDLE;
        end else begin
            case (state)
                IDLE: if (start) begin
                    mode <= use_hdrg;
                    state <= CNN;
                end
                CNN: if (cnn_done) begin
                    site <= 0;
                    state <= READ;
                end
                READ: begin
                    // The software also skips the local proposal on an empty syndrome.
                    local_x[site] <= decision[0] && (|{x_checks,z_checks});
                    local_z[site] <= decision[1] && (|{x_checks,z_checks});
                    if (site == 12) state <= ISSUE;
                    else site <= site + 1'b1;
                end
                ISSUE: state <= RETURN;
                RETURN: if (cleanup_done) begin
                    x_correction <= local_x ^ global_x;
                    z_correction <= local_z ^ global_z;
                    done <= 1;
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
