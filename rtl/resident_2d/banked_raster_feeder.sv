// Cyclic source banks and ten prior-row banks form parallel 11x11 windows.
// Within-beat forwarding handles cases where a padded row fits in one beat.
// Only read issue pauses; previously issued data continues through the pipeline.
module banked_raster_feeder #(
    parameter integer N = 61,
    parameter integer WORD_BITS = 4,
    parameter integer V = 2,
    parameter integer K = 11,
    parameter integer CW = (N < 2 ? 1 : $clog2(N))
) (
    input wire clk, input wire reset,
    input wire start, input wire frame_slot,
    input wire issue_enable, input wire [31:0] committed_count,
    input wire wr_slot,
    input wire [V-1:0] wr_valid,
    input wire [V*CW-1:0] wr_x, input wire [V*CW-1:0] wr_y,
    input wire [V*WORD_BITS-1:0] wr_data,
    output reg [V-1:0] window_valid,
    output reg [V*CW-1:0] window_x, output reg [V*CW-1:0] window_y,
    output reg [V*K*K*WORD_BITS-1:0] window_data,
    output reg scan_done
);
    localparam integer R = K/2;
    localparam integer OWN_WORDS = (N+V-1)/V;
    localparam integer PAD_N = N+2*R;
    localparam integer PAD_WORDS = (PAD_N+V-1)/V;
    localparam integer FRAME_DEPTH = N*OWN_WORDS;
    localparam integer DEPTH = 2*FRAME_DEPTH;
    // Valid px is nonnegative. The runtime quotient is therefore exactly
    // scan_word + floor((lane-R)/V); this lane offset folds at elaboration.
    // Explicit negative floor is required because signed SV division truncates.
    function automatic integer lane_word_offset(input integer lane_index);
        begin
            if (lane_index >= R) lane_word_offset = (lane_index-R)/V;
            else lane_word_offset = -((R-lane_index+V-1)/V);
        end
    endfunction
    reg scanning, active_slot;
    integer scan_y, scan_word;
    integer map_addr [0:V-1];
    reg [V-1:0] map_present;
    wire [WORD_BITS-1:0] map_q [0:V-1];
    integer lane, px, py, bank;
    always @* begin
        map_present = 0;
        for (integer b=0;b<V;b=b+1) map_addr[b]=0;
        for (integer l=0;l<V;l=l+1) begin
            px=scan_word*V+l-R;
            py=scan_y-R;
            bank=(l+V-(R%V))%V;
            if (px>=0 && px<N && py>=0 && py<N) begin
                map_present[l]=1;
                map_addr[bank]=active_slot*FRAME_DEPTH+py*OWN_WORDS+scan_word+lane_word_offset(l);
            end
        end
    end
    // Only a read issue is stalled. Existing synchronous read stages and
    // window/arithmetic pipelines continue to drain. A producer count denotes
    // words fully committed on PREVIOUS edges, never a same-edge write bypass.
    reg sources_ready;
    always @* begin
        sources_ready=1;
        for(integer q=0;q<V;q=q+1)
            if(map_present[q] && ((scan_y-R)*N+scan_word*V+q-R)>=committed_count)
                sources_ready=0;
    end
    wire issue=scanning && issue_enable && sources_ready;
    genvar b;
    generate for (b=0;b<V;b=b+1) begin: source_bank
        (* ram_style="block" *) reg [WORD_BITS-1:0] mem [0:DEPTH-1];
        reg [WORD_BITS-1:0] rd;
        integer wa;
        reg we;
        reg [WORD_BITS-1:0] wd;
        always @* begin
            wa=0;we=0;wd=0;
            for (integer l=0;l<V;l=l+1) begin
                if (wr_valid[l] && ((wr_x[l*CW+:CW]%V)==b)) begin
                    we=1;
                    wa=wr_slot*FRAME_DEPTH+wr_y[l*CW+:CW]*OWN_WORDS+wr_x[l*CW+:CW]/V;
                    wd=wr_data[l*WORD_BITS+:WORD_BITS];
                end
            end
        end
        always @(posedge clk) begin
            if (we) mem[wa] <= wd;
            if (issue) rd <= mem[map_addr[b]];
        end
        assign map_q[b]=rd;
    end endgenerate

    reg s1_active,s2_active;
    integer s1_y,s1_word,s2_y,s2_word;
    reg [V-1:0] s1_present;
    reg [V*WORD_BITS-1:0] input_d;
    wire [WORD_BITS-1:0] previous_rows [0:K-2][0:V-1];
    genvar r;
    generate for (r=0;r<K-1;r=r+1) begin: row_delay
        for (b=0;b<V;b=b+1) begin: row_bank
            (* ram_style="block" *) reg [WORD_BITS-1:0] mem [0:PAD_WORDS-1];
            reg [WORD_BITS-1:0] rd;
            wire [WORD_BITS-1:0] incoming;
            if (r==0) assign incoming=input_d[b*WORD_BITS+:WORD_BITS];
            else assign incoming=previous_rows[r-1][b];
            always @(posedge clk) begin
                if (s1_active) begin
                    if (s2_active && s1_word==s2_word) rd <= incoming;
                    else rd <= mem[s1_word];
                end
                if (s2_active) mem[s2_word] <= incoming;
            end
            assign previous_rows[r][b]=rd;
        end
    end endgenerate

    // All channels of a site are one word. Horizontal history is in logical
    // raster order. Each lane sees the columns of earlier lanes in the same beat.
    reg [WORD_BITS-1:0] history [0:K-1][0:K-2];
    reg [WORD_BITS-1:0] next_history [0:K-1][0:K-2];
    reg [WORD_BITS-1:0] column [0:K-1];
    reg [V*K*K*WORD_BITS-1:0] next_windows;
    always @* begin
        next_windows=0;
        for (integer ky=0;ky<K;ky=ky+1)
            for (integer kx=0;kx<K-1;kx=kx+1)
                next_history[ky][kx]=(s2_word==0)?{WORD_BITS{1'b0}}:history[ky][kx];
        for (integer l=0;l<V;l=l+1) begin
          if (s2_word*V+l<PAD_N) begin
            for (integer ky=0;ky<K;ky=ky+1) begin
                if (ky==K-1) column[ky]=input_d[l*WORD_BITS+:WORD_BITS];
                else column[ky]=previous_rows[K-2-ky][l];
                for (integer kx=0;kx<K-1;kx=kx+1)
                    next_windows[(l*K*K+ky*K+kx)*WORD_BITS+:WORD_BITS]=next_history[ky][kx];
                next_windows[(l*K*K+ky*K+K-1)*WORD_BITS+:WORD_BITS]=column[ky];
                for (integer kx=0;kx<K-2;kx=kx+1)
                    next_history[ky][kx]=next_history[ky][kx+1];
                next_history[ky][K-2]=column[ky];
            end
          end
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            scanning<=0;active_slot<=0;scan_y<=0;scan_word<=0;
            s1_active<=0;s2_active<=0;window_valid<=0;scan_done<=0;
            s1_y<=0;s1_word<=0;s2_y<=0;s2_word<=0;s1_present<=0;input_d<=0;
            window_x<=0;window_y<=0;window_data<=0;
            for (integer ky=0;ky<K;ky=ky+1)
                for (integer kx=0;kx<K-1;kx=kx+1) history[ky][kx]<=0;
        end else begin
            scan_done<=0;
            if (start) begin
                scanning<=1;active_slot<=frame_slot;scan_y<=0;scan_word<=0;
            end else if (issue) begin
                if (scan_word==PAD_WORDS-1) begin
                    scan_word<=0;
                    if (scan_y==PAD_N-1) scanning<=0;
                    else scan_y<=scan_y+1;
                end else scan_word<=scan_word+1;
            end
            s1_active<=issue;s1_y<=scan_y;s1_word<=scan_word;s1_present<=map_present;
            s2_active<=s1_active;s2_y<=s1_y;s2_word<=s1_word;
            for (integer l=0;l<V;l=l+1)
                input_d[l*WORD_BITS+:WORD_BITS]<=s1_present[l]?map_q[(l+V-(R%V))%V]:{WORD_BITS{1'b0}};
            window_valid<=0;
            if (s2_active) begin
                window_data<=next_windows;
                for (integer ky=0;ky<K;ky=ky+1)
                    for (integer kx=0;kx<K-1;kx=kx+1) history[ky][kx]<=next_history[ky][kx];
                for (integer l=0;l<V;l=l+1) begin
                    window_valid[l]<=(s2_y>=K-1 && s2_word*V+l>=K-1 && s2_word*V+l<PAD_N);
                    window_x[l*CW+:CW]<=s2_word*V+l-(K-1);
                    window_y[l*CW+:CW]<=s2_y-(K-1);
                end
                if (s2_y==PAD_N-1 && s2_word==PAD_WORDS-1) scan_done<=1;
            end
        end
    end
endmodule
