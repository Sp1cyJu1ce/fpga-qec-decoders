// Distance-33 interior worker used for the VU13P component study.
// The queues cross between fabric and worker clocks under a common reset.
module highend_endpoint(input wire core_clk_in,fabric_clk_in,reset,
 input wire[7:0] epoch,frame_id,
 input wire[319:0] rx_data,input wire rx_valid,output wire rx_ready,
 output wire[319:0] tx_data,output wire tx_valid,input wire tx_ready,
 output wire failed,output wire[7:0] error_code,output wire finished,
 output wire[31:0] accepted,emitted,compute_clocks0,compute_clocks1,compute_clocks2);
 wire core_clk,fabric_clk;
 BUFG core_clock_buffer(.I(core_clk_in),.O(core_clk));
 BUFG fabric_clock_buffer(.I(fabric_clk_in),.O(fabric_clk));
 wire[319:0] ingress_data,egress_data;
 wire ingress_valid,ingress_ready,egress_valid,egress_ready;
 wire ingress_wr_busy,ingress_rd_busy,egress_wr_busy,egress_rd_busy;
 wire tile_reset=ingress_rd_busy|egress_wr_busy;
 (* KEEP_HIERARCHY="yes" *) transport_async_fifo #(.WIDTH(320),.DEPTH(4),.LOCAL_RESET_BUSY(1)) ingress(
 .clk_a(fabric_clk),.clk_b(core_clk),.reset(reset),.tx_data(rx_data),.tx_valid(rx_valid),.tx_ready(rx_ready),
 .rx_data(ingress_data),.rx_valid(ingress_valid),.rx_ready(ingress_ready),.wr_reset_busy(ingress_wr_busy),.rd_reset_busy(ingress_rd_busy));
 tile_board #(.N(65),.T(17),.GRID(4),.ID(5),.M(27)) tile(
 .clk(core_clk),.reset(tile_reset),.epoch(epoch),.frame_id(frame_id),
 .rx_data(ingress_data),.rx_valid(ingress_valid),.rx_ready(ingress_ready),
 .tx_data(egress_data),.tx_valid(egress_valid),.tx_ready(egress_ready),
 .failed(failed),.error_code(error_code),.finished(finished),.accepted(accepted),.emitted(emitted),
 .compute_clocks0(compute_clocks0),.compute_clocks1(compute_clocks1),.compute_clocks2(compute_clocks2));
 (* KEEP_HIERARCHY="yes" *) transport_async_fifo #(.WIDTH(320),.DEPTH(4),.LOCAL_RESET_BUSY(1)) egress(
 .clk_a(core_clk),.clk_b(fabric_clk),.reset(reset),.tx_data(egress_data),.tx_valid(egress_valid),.tx_ready(egress_ready),
 .rx_data(tx_data),.rx_valid(tx_valid),.rx_ready(tx_ready),.wr_reset_busy(egress_wr_busy),.rd_reset_busy(egress_rd_busy));
endmodule
