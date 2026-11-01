// Complete-record queue between the worker and simulated fabric clocks.
// Reset flushes both sides together; unilateral resets are not supported.
// Ready/valid transfers occur on the corresponding local rising edge.
// The small asynchronous-read array targets registers or distributed RAM.
// Physical CDC constraints and a link PHY are outside this module.
module transport_async_fifo #(
    parameter integer WIDTH = 320,
    parameter integer DEPTH = 4,
    parameter integer ADDR_BITS = $clog2(DEPTH),
    parameter integer LOCAL_RESET_BUSY = 0
)(
    input wire clk_a, clk_b, reset,
    input wire [WIDTH-1:0] tx_data,
    input wire tx_valid,
    output wire tx_ready,
    output wire [WIDTH-1:0] rx_data,
    output wire rx_valid,
    input wire rx_ready,
    output wire wr_reset_busy, rd_reset_busy
);
    localparam integer PTR_BITS = ADDR_BITS + 1;
    reg [WIDTH-1:0] mem [0:DEPTH-1];
    reg [PTR_BITS-1:0] wbin, wgray, rbin, rgray;
    (* ASYNC_REG = "TRUE" *) reg [PTR_BITS-1:0] rgray_w1, rgray_w2;
    (* ASYNC_REG = "TRUE" *) reg [PTR_BITS-1:0] wgray_r1, wgray_r2;
    (* ASYNC_REG = "TRUE" *) reg [1:0] wr_release, rd_release;
    reg wfull, rempty;

    assign wr_reset_busy = LOCAL_RESET_BUSY ? wr_release[1] : (reset | wr_release[1]);
    assign rd_reset_busy = LOCAL_RESET_BUSY ? rd_release[1] : (reset | rd_release[1]);
    assign tx_ready = !wr_reset_busy && !wfull;
    assign rx_valid = !rd_reset_busy && !rempty;
    assign rx_data = mem[rbin[ADDR_BITS-1:0]];
    wire push = tx_valid && tx_ready;
    wire pop = rx_valid && rx_ready;
    wire [PTR_BITS-1:0] wbin_next = wbin + {{ADDR_BITS{1'b0}},push};
    wire [PTR_BITS-1:0] rbin_next = rbin + {{ADDR_BITS{1'b0}},pop};
    wire [PTR_BITS-1:0] wgray_next = (wbin_next >> 1) ^ wbin_next;
    wire [PTR_BITS-1:0] rgray_next = (rbin_next >> 1) ^ rbin_next;
    wire wfull_next = wgray_next ==
        {~rgray_w2[PTR_BITS-1:PTR_BITS-2],rgray_w2[PTR_BITS-3:0]};
    wire rempty_next = rgray_next == wgray_r2;

    // Async assertion; two local edges before reset busy can release.
    // Pointer/control registers remain zero on those release edges.
    always @(posedge clk_a or posedge reset)
        if (reset) wr_release <= 2'b11;
        else wr_release <= {wr_release[0],1'b0};
    always @(posedge clk_b or posedge reset)
        if (reset) rd_release <= 2'b11;
        else rd_release <= {rd_release[0],1'b0};

    always @(posedge clk_a or posedge reset) begin
        if (reset) begin
            wbin <= 0; wgray <= 0; wfull <= 0;
        end else if (wr_reset_busy) begin
            wbin <= 0; wgray <= 0; wfull <= 0;
        end else begin
            wbin <= wbin_next; wgray <= wgray_next; wfull <= wfull_next;
        end
    end
    always @(posedge clk_b or posedge reset) begin
        if (reset) begin
            rbin <= 0; rgray <= 0; rempty <= 1;
        end else if (rd_reset_busy) begin
            rbin <= 0; rgray <= 0; rempty <= 1;
        end else begin
            rbin <= rbin_next; rgray <= rgray_next; rempty <= rempty_next;
        end
    end
    // Synchronizer data paths are direct flop-to-flop. Sampling during local
    // release is permitted; pointer/control/ready-valid remain held as above.
    always @(posedge clk_a or posedge reset)
        if (reset) begin rgray_w1 <= 0; rgray_w2 <= 0; end
        else begin rgray_w1 <= rgray; rgray_w2 <= rgray_w1; end
    always @(posedge clk_b or posedge reset)
        if (reset) begin wgray_r1 <= 0; wgray_r2 <= 0; end
        else begin wgray_r1 <= wgray; wgray_r2 <= wgray_r1; end

    always @(posedge clk_a)
        if (push) mem[wbin[ADDR_BITS-1:0]] <= tx_data;

`ifndef SYNTHESIS
    initial begin
        if (WIDTH < 1 || DEPTH < 4 || (DEPTH & (DEPTH-1)) != 0 ||
            (1 << ADDR_BITS) != DEPTH)
            $fatal(1,"FIFO parameters require WIDTH>=1 and power-of-two DEPTH>=4");
    end
`endif
endmodule
