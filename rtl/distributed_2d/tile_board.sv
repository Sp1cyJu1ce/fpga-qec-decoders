// One balanced partition of a three-layer spatial decoder.
// Each layer waits for its radius-five input halo, then computes its owned sites.
// Intermediate outputs are sent back to the fabric for delivery to neighbours.
// This endpoint does not implement a physical transceiver or the global decoder.
module tile_board #(
 parameter integer N=11,T=6,GRID=2,ID=0,M=T+10,CW=$clog2(M)
)(
 input wire clk,reset,
 input wire [7:0] epoch,frame_id,
 input wire [319:0] rx_data,input wire rx_valid,output wire rx_ready,
 output wire [319:0] tx_data,output wire tx_valid,input wire tx_ready,
 output reg failed,output reg [7:0] error_code,output reg finished,
 output reg [31:0] accepted,emitted,
 output reg [31:0] compute_clocks0,compute_clocks1,compute_clocks2
);
 localparam integer BASE=N/GRID,EXTRA=N%GRID;
 localparam integer IX=ID%GRID,IY=ID/GRID;
 localparam integer OX=IX*BASE+(IX<EXTRA?IX:EXTRA),OY=IY*BASE+(IY<EXTRA?IY:EXTRA);
 localparam integer TW=BASE+(IX<EXTRA),TH=BASE+(IY<EXTRA);
 function automatic integer owner_axis(input integer c);
  begin owner_axis=(c<(BASE+1)*EXTRA)?c/(BASE+1):EXTRA+(c-(BASE+1)*EXTRA)/BASE;end
 endfunction
 localparam integer XLO=(OX<5?0:OX-5),YLO=(OY<5?0:OY-5);
 localparam integer XHI=(OX+TW+5>N?N:OX+TW+5),YHI=(OY+TH+5>N?N:OY+TH+5);
 localparam integer REQUIRED=(XHI-XLO)*(YHI-YLO);
 localparam CLEAR=0,WAIT_INPUT=1,ARM=2,RUN=3,SEND=4,DONE=5;
 reg [2:0] state;reg [1:0] phase;
 integer clear_pos,received[0:2],core_index,scan_count,clocks;
 reg seen[0:2][0:M*M-1];
 reg [191:0] core_mem[0:T*T-1];
 function automatic [31:0] crc32(input [279:0] body);
  reg [31:0] c;integer i;
  begin c=32'hffffffff;for(i=0;i<280;i=i+1)c=(c>>1)^((c[0]^body[i])?32'hedb88320:32'b0);crc32=c^32'hffffffff;end
 endfunction
 wire [1:0] rlayer=rx_data[233:232];
 wire [15:0] gx=rx_data[207:192],gy=rx_data[223:208];
 wire [7:0] rsource=rx_data[241:234];
 integer lx,ly,rpos;reg [7:0] packet_error;
 always @* begin
  lx=$signed({1'b0,gx})-OX+5;ly=$signed({1'b0,gy})-OY+5;rpos=ly*M+lx;
  packet_error=0;
  if(rx_data[319:312]!=1 || rx_data[279:260]!=0 || rx_data[259:258]!=0)packet_error=1;
  else if(crc32(rx_data[279:0])!=rx_data[311:280])packet_error=2;
  else if(rx_data[257:250]!=epoch || rx_data[231:224]!=frame_id)packet_error=3;
  else if(rx_data[249:242]!=ID || rlayer>2)packet_error=4;
  else if(gx>=N || gy>=N || gx<XLO || gx>=XHI || gy<YLO || gy>=YHI || lx<0 || lx>=M || ly<0 || ly>=M)packet_error=5;
  else if((rlayer==0 && rsource!=255) || (rlayer!=0 && rsource!=owner_axis(gy)*GRID+owner_axis(gx)))packet_error=6;
  else if(seen[rlayer][rpos])packet_error=7;
  else if((rlayer==0 && rx_data[191:4]!=0) || (rlayer==1 && rx_data[191:99]!=0) || (rlayer==2 && rx_data[191:189]!=0))packet_error=8;
 end
 assign rx_ready=!reset && !failed && state!=CLEAR && state!=DONE;
 wire receive_good=rx_valid && rx_ready && packet_error==0;
 wire [CW-1:0] wx=(state==CLEAR)?clear_pos%M:lx;
 wire [CW-1:0] wy=(state==CLEAR)?clear_pos/M:ly;
 wire [2:0] wr={(state==CLEAR)||(receive_good&&rlayer==2),(state==CLEAR)||(receive_good&&rlayer==1),(state==CLEAR)||(receive_good&&rlayer==0)};
 wire [191:0] wd=state==CLEAR?192'b0:rx_data[191:0];
 wire [2:0] ov;wire [CW-1:0] ox[0:2],oy[0:2];
 wire [98:0] a0;wire [188:0] a1;wire [1:0] a2;
 retained_layer_stage #(.N(M),.V(1),.LAYER(1),.CIN(4),.COUT(9),.IN_BITS(1),.OUT_BITS(11),.ACC_BITS(16),.LATENCY(11)) l1(
  .clk(clk),.reset(reset),.start(state==ARM&&phase==0),.slot(1'b0),.wr_slot(1'b0),.wr_valid(wr[0]),.wr_x(wx),.wr_y(wy),.wr_data(wd[3:0]),.out_valid(ov[0]),.out_x(ox[0]),.out_y(oy[0]),.out_data(a0));
 retained_layer_stage #(.N(M),.V(1),.LAYER(2),.CIN(9),.COUT(9),.IN_BITS(11),.OUT_BITS(21),.ACC_BITS(24),.LATENCY(13)) l2(
  .clk(clk),.reset(reset),.start(state==ARM&&phase==1),.slot(1'b0),.wr_slot(1'b0),.wr_valid(wr[1]),.wr_x(wx),.wr_y(wy),.wr_data(wd[98:0]),.out_valid(ov[1]),.out_x(ox[1]),.out_y(oy[1]),.out_data(a1));
 retained_layer_stage #(.N(M),.V(1),.LAYER(3),.CIN(9),.COUT(2),.IN_BITS(21),.OUT_BITS(1),.ACC_BITS(36),.LATENCY(13)) l3(
  .clk(clk),.reset(reset),.start(state==ARM&&phase==2),.slot(1'b0),.wr_slot(1'b0),.wr_valid(wr[2]),.wr_x(wx),.wr_y(wy),.wr_data(wd[188:0]),.out_valid(ov[2]),.out_x(ox[2]),.out_y(oy[2]),.out_data(a2));
 wire [191:0] activation=phase==0?{{93{1'b0}},a0}:phase==1?{3'b0,a1}:{{190{1'b0}},a2};
 integer cx,cy,global_x,global_y,tx_x,tx_y;
 reg [279:0] tx_body;
 always @* begin
  cx=ox[phase]-5;cy=oy[phase]-5;global_x=OX+cx;global_y=OY+cy;
  tx_x=OX+core_index%T;tx_y=OY+core_index/T;
  tx_body=0;tx_body[191:0]=core_mem[core_index];
  tx_body[207:192]=tx_x;tx_body[223:208]=tx_y;
  tx_body[231:224]=frame_id;tx_body[233:232]=phase+1;
  tx_body[241:234]=ID;tx_body[249:242]=255;tx_body[257:250]=epoch;
  tx_body[259:258]=(phase==2)?2:0;
 end
 assign tx_valid=!reset && !failed && state==SEND && core_index%T<TW && core_index/T<TH && tx_x<N && tx_y<N;
 assign tx_data={8'h01,crc32(tx_body),tx_body};
 always @(posedge clk)begin
  if(reset)begin
   state<=CLEAR;phase<=0;clear_pos<=0;core_index<=0;scan_count<=0;clocks<=0;
   failed<=0;error_code<=0;finished<=0;accepted<=0;emitted<=0;
   compute_clocks0<=0;compute_clocks1<=0;compute_clocks2<=0;
   for(integer k=0;k<3;k=k+1)received[k]<=0;
  end else if(!failed)begin
   if(rx_valid && rx_ready)begin
    if(packet_error!=0)begin failed<=1;error_code<=packet_error;end
    else begin seen[rlayer][rpos]<=1;received[rlayer]<=received[rlayer]+1;accepted<=accepted+1;end
   end
   case(state)
    CLEAR:begin
     for(integer k=0;k<3;k=k+1)seen[k][clear_pos]<=0;
     if(clear_pos==M*M-1)begin state<=WAIT_INPUT;clear_pos<=0;end else clear_pos<=clear_pos+1;
    end
    WAIT_INPUT:if(received[phase]==REQUIRED)state<=ARM;
    ARM:begin state<=RUN;scan_count<=0;clocks<=0;end
    RUN:begin
     clocks<=clocks+1;
     if(ov[phase])begin
      if(cx>=0 && cx<TW && cy>=0 && cy<TH && global_x<N && global_y<N)core_mem[cy*T+cx]<=activation;
      scan_count<=scan_count+1;
      if(scan_count==M*M-1)begin
       state<=SEND;core_index<=0;
       case(phase)0:compute_clocks0<=clocks+1;1:compute_clocks1<=clocks+1;2:compute_clocks2<=clocks+1;endcase
      end
     end
    end
    SEND:if((core_index%T>=TW || core_index/T>=TH || tx_x>=N || tx_y>=N) || (tx_valid && tx_ready))begin
     if(tx_valid&&tx_ready)emitted<=emitted+1;
     if(core_index==T*T-1)begin
      core_index<=0;
      if(phase==2)begin state<=DONE;finished<=1;end else begin phase<=phase+1;state<=WAIT_INPUT;end
     end else core_index<=core_index+1;
    end
    default:;
   endcase
  end
 end
endmodule
