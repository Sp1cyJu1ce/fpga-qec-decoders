// Distance-33 resident worker used for the VP1902 component study.
// BUFG is supplied by the AMD device library; board interfaces are external.
module highend_worker_core #(
 parameter integer N=65,V=3,CW=(N<2?1:$clog2(N)),BW=(V<2?1:$clog2(V))
)(
 input wire clk,reset,start,frame_slot,
 input wire [2:0] issue_enable,
 input wire [V-1:0] load_valid,
 input wire [V*CW-1:0] load_x,load_y,
 input wire [V*4-1:0] load_data,
 output wire load_ready,busy,
 output reg done,output reg [31:0] elapsed_cycles,
 output wire [V-1:0] result_valid,
 output wire [V*CW-1:0] result_x,result_y,
 output wire [V*2-1:0] result_data,
 input wire result_read_enable,result_read_slot,
 input wire [CW-1:0] result_read_x,result_read_y,
 output reg result_read_valid,output wire [1:0] result_read_data
);
wire buffered_clk;
BUFG clock_buffer(.I(clk),.O(buffered_clk));
worker_rowready #(.N(N),.V(V)) core(
.clk(buffered_clk),
.reset(reset),
.start(start),
.frame_slot(frame_slot),
.issue_enable(issue_enable),
.load_valid(load_valid),
.load_x(load_x),
.load_y(load_y),
.load_data(load_data),
.load_ready(load_ready),
.busy(busy),
.done(done),
.elapsed_cycles(elapsed_cycles),
.result_valid(result_valid),
.result_x(result_x),
.result_y(result_y),
.result_data(result_data),
.result_read_enable(result_read_enable),
.result_read_slot(result_read_slot),
.result_read_x(result_read_x),
.result_read_y(result_read_y),
.result_read_valid(result_read_valid),
.result_read_data(result_read_data)
);
endmodule
