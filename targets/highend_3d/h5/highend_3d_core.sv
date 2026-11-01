// Complete finite-volume core from the retained high-end implementation.
// This boundary has no physical board pin or link assignment.
module highend_3d_core(input wire clk,rst,syn_we,start,
 input wire[15:0] syn_addr,dec_raddr,input wire[11:0] syn_word,
 output wire busy,done,output wire[31:0] latency_cycles,output wire[11:0] dec_q);
 wire buffered_clk;BUFG core_clock_buffer(.I(clk),.O(buffered_clk));
 decoder_top_stream_3d #(.D(49),.TCO_H(64)) core(.clk(buffered_clk),.rst(rst),.syn_we(syn_we),
 .syn_addr(syn_addr),.syn_word(syn_word),.start(start),.busy(busy),.done(done),
 .latency_cycles(latency_cycles),.dec_raddr(dec_raddr),.dec_q(dec_q));
endmodule
