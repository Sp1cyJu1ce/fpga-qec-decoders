// One loaded tile layer with fixed trained arithmetic and aligned coordinates.
// tile_board starts this stage after the complete input halo has arrived.
// LATENCY must match the selected hardwired kernel.
module retained_layer_stage #(
    parameter integer N=61, V=2, LAYER=1, CIN=4, COUT=9,
    parameter integer IN_BITS=1, OUT_BITS=11, ACC_BITS=16, LATENCY=11,
    parameter integer CW=(N<2?1:$clog2(N))
) (
    input wire clk, input wire reset, input wire start, input wire slot,
    input wire wr_slot, input wire [V-1:0] wr_valid,
    input wire [V*CW-1:0] wr_x, input wire [V*CW-1:0] wr_y,
    input wire [V*CIN*IN_BITS-1:0] wr_data,
    output wire [V-1:0] out_valid,
    output wire [V*CW-1:0] out_x, output wire [V*CW-1:0] out_y,
    output wire [V*COUT*OUT_BITS-1:0] out_data
);
    localparam integer WINDOW_BITS=121*CIN*IN_BITS;
    wire [V-1:0] wvalid;
    wire [V*CW-1:0] wx,wy;
    wire [V*WINDOW_BITS-1:0] window_data;
    wire scan_done;
    banked_raster_feeder #(.N(N),.WORD_BITS(CIN*IN_BITS),.V(V),.K(11)) feeder (
        .clk(clk),.reset(reset),.start(start),.frame_slot(slot),
        .wr_slot(wr_slot),.wr_valid(wr_valid),.wr_x(wr_x),.wr_y(wr_y),.wr_data(wr_data),
        .window_valid(wvalid),.window_x(wx),.window_y(wy),.window_data(window_data),.scan_done(scan_done)
    );
    reg [V-1:0] valid_pipe [0:LATENCY];
    reg [V*CW-1:0] x_pipe [0:LATENCY],y_pipe [0:LATENCY];
    always @(posedge clk) begin
        if (reset) begin
            for (integer p=0;p<=LATENCY;p=p+1) begin
                valid_pipe[p]<=0;x_pipe[p]<=0;y_pipe[p]<=0;
            end
        end else begin
            valid_pipe[0]<=wvalid;x_pipe[0]<=wx;y_pipe[0]<=wy;
            for (integer p=1;p<=LATENCY;p=p+1) begin
                valid_pipe[p]<=valid_pipe[p-1];x_pipe[p]<=x_pipe[p-1];y_pipe[p]<=y_pipe[p-1];
            end
        end
    end
    assign out_valid=valid_pipe[LATENCY];
    assign out_x=x_pipe[LATENCY];assign out_y=y_pipe[LATENCY];
    genvar lane;
    generate for (lane=0;lane<V;lane=lane+1) begin: arithmetic
        wire [COUT*ACC_BITS-1:0] total_observable_in_simulation;
        if (LAYER==1) begin
            hardwired_L1 kernel(.clk(clk),.x(window_data[lane*WINDOW_BITS+:WINDOW_BITS]),
                .total(total_observable_in_simulation),.y(out_data[lane*COUT*OUT_BITS+:COUT*OUT_BITS]));
        end else if (LAYER==2) begin
            hardwired_L2 kernel(.clk(clk),.x(window_data[lane*WINDOW_BITS+:WINDOW_BITS]),
                .total(total_observable_in_simulation),.y(out_data[lane*COUT*OUT_BITS+:COUT*OUT_BITS]));
        end else begin
            hardwired_L3 kernel(.clk(clk),.x(window_data[lane*WINDOW_BITS+:WINDOW_BITS]),
                .total(total_observable_in_simulation),.y(out_data[lane*COUT*OUT_BITS+:COUT*OUT_BITS]));
        end
    end endgenerate
endmodule
