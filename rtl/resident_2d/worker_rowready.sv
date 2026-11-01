// Three overlapping layers over a preloaded feature map.
// A downstream layer reads only sites committed on an earlier clock edge.
// issue_enable pauses new reads while the arithmetic pipelines drain.
// Result outputs cannot stall; the retained result banks support later readback.
module worker_rowready #(
 parameter integer N=61,V=2,CW=(N<2?1:$clog2(N)),BW=(V<2?1:$clog2(V))
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
 reg [1:0] state;
 reg resident_slot;
 reg [31:0] committed1,committed2,committed3,cycles;
 wire active_slot=(state==0)?frame_slot:resident_slot;
 wire [V-1:0] v1,v2,v3;
 wire [V*CW-1:0] x1,y1,x2,y2,x3,y3;
 wire [V*99-1:0] a1;
 wire [V*189-1:0] a2;
 wire [V*2-1:0] a3;
 integer n1,n2,n3;
 always @* begin
  n1=0;n2=0;n3=0;
  for(integer k=0;k<V;k=k+1)begin n1=n1+v1[k];n2=n2+v2[k];n3=n3+v3[k];end
 end
 wire finish3=(state!=0 && committed3+n3==N*N && n3!=0);
 assign busy=(state!=0);assign load_ready=(state==0);
 retained_layer_stage #(.N(N),.V(V),.LAYER(1),.CIN(4),.COUT(9),.IN_BITS(1),.OUT_BITS(11),.ACC_BITS(16),.LATENCY(11)) l1 (
  .clk(clk),.reset(reset),.start(start && state==0),.slot(active_slot),
  .wr_slot(frame_slot),.wr_valid(load_ready?load_valid:{V{1'b0}}),.wr_x(load_x),.wr_y(load_y),.wr_data(load_data),
  .issue_enable(issue_enable[0]),.committed_count(N*N),
  .out_valid(v1),.out_x(x1),.out_y(y1),.out_data(a1));
 retained_layer_stage #(.N(N),.V(V),.LAYER(2),.CIN(9),.COUT(9),.IN_BITS(11),.OUT_BITS(21),.ACC_BITS(24),.LATENCY(13)) l2 (
  .clk(clk),.reset(reset),.start(start && state==0),.slot(active_slot),
  .wr_slot(active_slot),.wr_valid(busy?v1:{V{1'b0}}),.wr_x(x1),.wr_y(y1),.wr_data(a1),
  .issue_enable(issue_enable[1]),.committed_count(committed1),
  .out_valid(v2),.out_x(x2),.out_y(y2),.out_data(a2));
 retained_layer_stage #(.N(N),.V(V),.LAYER(3),.CIN(9),.COUT(2),.IN_BITS(21),.OUT_BITS(1),.ACC_BITS(36),.LATENCY(13)) l3 (
  .clk(clk),.reset(reset),.start(start && state==0),.slot(active_slot),
  .wr_slot(active_slot),.wr_valid(busy?v2:{V{1'b0}}),.wr_x(x2),.wr_y(y2),.wr_data(a2),
  .issue_enable(issue_enable[2]),.committed_count(committed2),
  .out_valid(v3),.out_x(x3),.out_y(y3),.out_data(a3));
 assign result_valid=busy?v3:{V{1'b0}};
 assign result_x=x3;assign result_y=y3;assign result_data=a3;
 localparam integer RESULT_WORDS=(N+V-1)/V, RESULT_FRAME_DEPTH=N*RESULT_WORDS;
 wire [1:0] result_rd[0:V-1];reg [BW-1:0] result_read_bank;
 genvar rb;
 generate for(rb=0;rb<V;rb=rb+1)begin:result_bank
  (* ram_style="block" *) reg [1:0] mem[0:2*RESULT_FRAME_DEPTH-1];
  reg [1:0] rd;integer wa;reg we;reg [1:0] wd;
  always @* begin
   wa=0;we=0;wd=0;
   for(integer l=0;l<V;l=l+1)if(result_valid[l] && result_x[l*CW+:CW]%V==rb)begin
    we=1;wa=active_slot*RESULT_FRAME_DEPTH+result_y[l*CW+:CW]*RESULT_WORDS+result_x[l*CW+:CW]/V;wd=result_data[l*2+:2];
   end
  end
  always @(posedge clk)begin
   if(we)mem[wa]<=wd;
   if(result_read_enable && !busy)rd<=mem[result_read_slot*RESULT_FRAME_DEPTH+result_read_y*RESULT_WORDS+result_read_x/V];
  end
  assign result_rd[rb]=rd;
 end endgenerate
 assign result_read_data=result_rd[result_read_bank];
 always @(posedge clk)begin
  if(reset)begin result_read_valid<=0;result_read_bank<=0;end
  else begin result_read_valid<=result_read_enable && !busy;
   if(result_read_enable && !busy)result_read_bank<=result_read_x%V;
  end
 end
 always @(posedge clk)begin
  if(reset)begin
   state<=0;resident_slot<=0;committed1<=0;committed2<=0;committed3<=0;
   cycles<=0;done<=0;elapsed_cycles<=0;
  end else begin
   done<=0;
   if(state==0)begin
    if(start)begin
     state<=1;resident_slot<=frame_slot;committed1<=0;committed2<=0;committed3<=0;cycles<=0;
    end
   end else begin
    cycles<=cycles+1;
    committed1<=committed1+n1;committed2<=committed2+n2;committed3<=committed3+n3;
    if(finish3)begin state<=0;done<=1;elapsed_cycles<=cycles+1;end
   end
  end
 end
endmodule
