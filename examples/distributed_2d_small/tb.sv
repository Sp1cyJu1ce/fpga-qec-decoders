`timescale 1ns/1ps
module tb;
localparam N=5,T=3,GRID=2,B=GRID*GRID,M=T+10,F=6;
localparam LOCAL_RESET_BUSY=0;
reg fabric_clk=0;reg [B-1:0] bclk=0;reg reset=1;
integer scenario=0,fault=0,mutate=0,abort_test=0,frame=0,mutate_network=0,fabric_period_ns=10,core_period_ns=10,service_divider=1,sync_tile_reset=0,mutate_service=0;reg [7:0] epoch=1;
integer counts[0:B-1][0:2],allcounts[0:B-1][0:2];
integer trace_fd;string trace_path="packets.csv";
initial begin
 if($value$plusargs("SCENARIO=%d",scenario))begin end
 if($value$plusargs("FABRIC_NS=%d",fabric_period_ns))begin end
 if($value$plusargs("CORE_NS=%d",core_period_ns))begin end
 if($value$plusargs("SERVICE_DIV=%d",service_divider))begin end
 if($value$plusargs("SYNC_TILE_RESET=%d",sync_tile_reset))begin end
 if($value$plusargs("MUTATE_SERVICE=%d",mutate_service))begin end
 if(fabric_period_ns<2 || fabric_period_ns%2!=0)$fatal(1,"INVALID_FABRIC_PERIOD");
 if(core_period_ns<2 || core_period_ns%2!=0)$fatal(1,"INVALID_CORE_PERIOD");
 if(service_divider<1 || B%service_divider!=0)$fatal(1,"INVALID_SERVICE_DIVIDER");
 if(sync_tile_reset!=LOCAL_RESET_BUSY)$fatal(1,"RESET_SHELL_MODE_MISMATCH");
 $display("TRANSPORT_CONFIG scenario=%0d fabric_period_ns=%0d core_period_ns=%0d service_divider=%0d sync_tile_reset=%0d local_reset_busy=%0d",scenario,(scenario==0?fabric_period_ns:6),core_period_ns,service_divider,sync_tile_reset,LOCAL_RESET_BUSY);
 if($value$plusargs("FAULT=%d",fault))begin end
 if($value$plusargs("MUTATE=%d",mutate))begin end
 if($value$plusargs("MUTATE_NETWORK=%d",mutate_network))begin end
 if($value$plusargs("ABORT=%d",abort_test))begin end
 if($value$plusargs("TRACE=%s",trace_path))begin end
 trace_fd=$fopen(trace_path,"w");if(!trace_fd)$fatal(1,"TRACE_OPEN_FAIL");
 $fwrite(trace_fd,"event,frame,epoch,board,time_ps,packet_hex\n");
end
initial begin #1;forever #(scenario==0?fabric_period_ns/2:3)fabric_clk=~fabric_clk;end
for(genvar ck=0;ck<B;ck=ck+1)begin:clocks
 initial begin #(1+ck%3);forever #(scenario==0?core_period_ns/2:scenario==1?4+ck%4:5+2*(ck%5))bclk[ck]=~bclk[ck];end
end
reg [31:0] service_tick=0;reg service_was_reset=1;
always @(posedge fabric_clk)begin
 if(reset)begin service_tick<=0;service_was_reset<=1;end
 else begin
  if(service_was_reset)$display("SERVICE_EPOCH frame=%0d epoch=%0d phase_ps=%0t",frame,epoch,$time);
  service_was_reset<=0;service_tick<=service_tick+1;
 end
end
wire [B-1:0] rx_valid,rx_ready,tx_valid,tx_ready,failed,finished;
wire [319:0] rx_data[0:B-1],tx_data[0:B-1];
reg [B-1:0] network_valid,network_ready;wire [B-1:0] network_accept,network_output;
reg [319:0] network_data[0:B-1];wire [319:0] network_return[0:B-1];
wire [7:0] errors[0:B-1];wire [31:0] accepted[0:B-1],emitted[0:B-1],c0[0:B-1],c1[0:B-1],c2[0:B-1];
for(genvar b=0;b<B;b=b+1)begin:boards
 wire wb1,rb1,wb2,rb2;
 wire ingress_ready_ungated,egress_valid_ungated;
 wire natural_service_slot=(service_tick%service_divider)==(b%service_divider);
 wire service_slot=natural_service_slot || (mutate_service && b==0);
 assign network_accept[b]=ingress_ready_ungated && service_slot;
 assign network_output[b]=egress_valid_ungated && service_slot;
 always @(posedge fabric_clk)if(!reset && !natural_service_slot &&
  ((network_valid[b]&&network_accept[b]) || (network_output[b]&&network_ready[b])))
   $fatal(1,"SERVICE_PHASE_FAIL board=%0d tick=%0d",b,service_tick);
 transport_async_fifo #(.WIDTH(320),.DEPTH(4),.LOCAL_RESET_BUSY(LOCAL_RESET_BUSY)) ingress(.clk_a(fabric_clk),.clk_b(bclk[b]),.reset(reset),.tx_data(network_data[b]),.tx_valid(network_valid[b]&&service_slot),.tx_ready(ingress_ready_ungated),.rx_data(rx_data[b]),.rx_valid(rx_valid[b]),.rx_ready(rx_ready[b]),.wr_reset_busy(wb1),.rd_reset_busy(rb1));
 tile_board #(.N(N),.T(T),.GRID(GRID),.ID(b)) dut(.clk(bclk[b]),.reset(reset),.epoch(epoch),.frame_id(frame[7:0]),.rx_data(rx_data[b]),.rx_valid(rx_valid[b]),.rx_ready(rx_ready[b]),.tx_data(tx_data[b]),.tx_valid(tx_valid[b]),.tx_ready(tx_ready[b]),.failed(failed[b]),.error_code(errors[b]),.finished(finished[b]),.accepted(accepted[b]),.emitted(emitted[b]),.compute_clocks0(c0[b]),.compute_clocks1(c1[b]),.compute_clocks2(c2[b]));
 transport_async_fifo #(.WIDTH(320),.DEPTH(4),.LOCAL_RESET_BUSY(LOCAL_RESET_BUSY)) egress(.clk_a(bclk[b]),.clk_b(fabric_clk),.reset(reset),.tx_data(tx_data[b]),.tx_valid(tx_valid[b]),.tx_ready(tx_ready[b]),.rx_data(network_return[b]),.rx_valid(egress_valid_ungated),.rx_ready(network_ready[b]&&service_slot),.wr_reset_busy(wb2),.rd_reset_busy(rb2));
 integer txstall=0;
 reg previous_stall=0;reg [319:0] held;
 always @(posedge bclk[b])if(reset)begin txstall=0;previous_stall=0;end else begin
  if(tx_valid[b]&&tx_ready[b])$fwrite(trace_fd,"TX,%0d,%0d,%0d,%0t,%080h\n",frame,epoch,b,$time,tx_data[b]);
  if(rx_valid[b]&&rx_ready[b])$fwrite(trace_fd,"RX,%0d,%0d,%0d,%0t,%080h\n",frame,epoch,b,$time,rx_data[b]);
  if(previous_stall && (!tx_valid[b] || tx_data[b]!==held))$fatal(1,"TX_STABILITY_FAIL board=%0d",b);
  previous_stall=tx_valid[b]&&!tx_ready[b];held=tx_data[b];if(previous_stall)txstall=txstall+1;
 end
end
reg [3:0] inputs[0:F*N*N-1];initial $readmemh("inputs.hex",inputs);
reg [98:0] expected1[0:F*N*N-1];
reg [143:0] totals1[0:F*N*N-1];
initial begin $readmemh("layer1_y.hex",expected1);$readmemh("layer1_total.hex",totals1);end
for(genvar q1=0;q1<B;q1=q1+1)begin:check_layer1
 reg seen[0:N*N-1];reg local_seen[0:M*M-1];integer x,y,p,lp;
 always @(posedge bclk[q1])begin
  if(reset)begin counts[q1][0]=0;allcounts[q1][0]=0;for(integer z=0;z<N*N;z=z+1)seen[z]=0;for(integer z=0;z<M*M;z=z+1)local_seen[z]=0;end
  else if(boards[q1].dut.state==3 && boards[q1].dut.phase==0 && boards[q1].dut.ov[0])begin
   x=boards[q1].dut.ox[0];y=boards[q1].dut.oy[0];lp=y*M+x;
   if(x<0||x>=M||y<0||y>=M||local_seen[lp])$fatal(1,"LOCAL_COORD_FAIL");
   local_seen[lp]=1;allcounts[q1][0]=allcounts[q1][0]+1;
   if(x>=5 && x<boards[q1].dut.TW+5 && y>=5 && y<boards[q1].dut.TH+5)begin
    x=x-5+boards[q1].dut.OX;y=y-5+boards[q1].dut.OY;p=y*N+x;
    if(x<N && y<N)begin
     if(seen[p])$fatal(1,"OWNERSHIP_DUPLICATE");seen[p]=1;counts[q1][0]=counts[q1][0]+1;
     if(boards[q1].dut.a0!==expected1[frame*N*N+p])$fatal(1,"ACTIVATION_FAIL layer=1 board=%0d frame=%0d p=%0d",q1,frame,p);
     if(boards[q1].dut.l1.arithmetic[0].total_observable_in_simulation!==(totals1[frame*N*N+p]^((1==1&&p==0&&mutate)?1:0)))$fatal(1,"RAW_TOTAL_FAIL layer=1 board=%0d frame=%0d p=%0d",q1,frame,p);
    end
   end
  end
 end
end

reg [188:0] expected2[0:F*N*N-1];
reg [215:0] totals2[0:F*N*N-1];
initial begin $readmemh("layer2_y.hex",expected2);$readmemh("layer2_total.hex",totals2);end
for(genvar q2=0;q2<B;q2=q2+1)begin:check_layer2
 reg seen[0:N*N-1];reg local_seen[0:M*M-1];integer x,y,p,lp;
 always @(posedge bclk[q2])begin
  if(reset)begin counts[q2][1]=0;allcounts[q2][1]=0;for(integer z=0;z<N*N;z=z+1)seen[z]=0;for(integer z=0;z<M*M;z=z+1)local_seen[z]=0;end
  else if(boards[q2].dut.state==3 && boards[q2].dut.phase==1 && boards[q2].dut.ov[1])begin
   x=boards[q2].dut.ox[1];y=boards[q2].dut.oy[1];lp=y*M+x;
   if(x<0||x>=M||y<0||y>=M||local_seen[lp])$fatal(1,"LOCAL_COORD_FAIL");
   local_seen[lp]=1;allcounts[q2][1]=allcounts[q2][1]+1;
   if(x>=5 && x<boards[q2].dut.TW+5 && y>=5 && y<boards[q2].dut.TH+5)begin
    x=x-5+boards[q2].dut.OX;y=y-5+boards[q2].dut.OY;p=y*N+x;
    if(x<N && y<N)begin
     if(seen[p])$fatal(1,"OWNERSHIP_DUPLICATE");seen[p]=1;counts[q2][1]=counts[q2][1]+1;
     if(boards[q2].dut.a1!==expected2[frame*N*N+p])$fatal(1,"ACTIVATION_FAIL layer=2 board=%0d frame=%0d p=%0d",q2,frame,p);
     if(boards[q2].dut.l2.arithmetic[0].total_observable_in_simulation!==(totals2[frame*N*N+p]^((2==1&&p==0&&mutate)?1:0)))$fatal(1,"RAW_TOTAL_FAIL layer=2 board=%0d frame=%0d p=%0d",q2,frame,p);
    end
   end
  end
 end
end

reg [1:0] expected3[0:F*N*N-1];
reg [71:0] totals3[0:F*N*N-1];
initial begin $readmemh("layer3_y.hex",expected3);$readmemh("layer3_total.hex",totals3);end
for(genvar q3=0;q3<B;q3=q3+1)begin:check_layer3
 reg seen[0:N*N-1];reg local_seen[0:M*M-1];integer x,y,p,lp;
 always @(posedge bclk[q3])begin
  if(reset)begin counts[q3][2]=0;allcounts[q3][2]=0;for(integer z=0;z<N*N;z=z+1)seen[z]=0;for(integer z=0;z<M*M;z=z+1)local_seen[z]=0;end
  else if(boards[q3].dut.state==3 && boards[q3].dut.phase==2 && boards[q3].dut.ov[2])begin
   x=boards[q3].dut.ox[2];y=boards[q3].dut.oy[2];lp=y*M+x;
   if(x<0||x>=M||y<0||y>=M||local_seen[lp])$fatal(1,"LOCAL_COORD_FAIL");
   local_seen[lp]=1;allcounts[q3][2]=allcounts[q3][2]+1;
   if(x>=5 && x<boards[q3].dut.TW+5 && y>=5 && y<boards[q3].dut.TH+5)begin
    x=x-5+boards[q3].dut.OX;y=y-5+boards[q3].dut.OY;p=y*N+x;
    if(x<N && y<N)begin
     if(seen[p])$fatal(1,"OWNERSHIP_DUPLICATE");seen[p]=1;counts[q3][2]=counts[q3][2]+1;
     if(boards[q3].dut.a2!==expected3[frame*N*N+p])$fatal(1,"ACTIVATION_FAIL layer=3 board=%0d frame=%0d p=%0d",q3,frame,p);
     if(boards[q3].dut.l3.arithmetic[0].total_observable_in_simulation!==(totals3[frame*N*N+p]^((3==1&&p==0&&mutate)?1:0)))$fatal(1,"RAW_TOTAL_FAIL layer=3 board=%0d frame=%0d p=%0d",q3,frame,p);
    end
   end
  end
 end
end

function automatic [31:0] crc32(input [279:0] body);
 reg [31:0] c;integer i;begin c=32'hffffffff;for(i=0;i<280;i=i+1)c=(c>>1)^((c[0]^body[i])?32'hedb88320:32'b0);crc32=c^32'hffffffff;end
endfunction
function automatic [319:0] packet(input [191:0] data,input integer x,y,layer,src,dst,kind);
 reg [279:0] v;begin v=0;v[191:0]=data;v[207:192]=x;v[223:208]=y;v[231:224]=frame;v[233:232]=layer;v[241:234]=src;v[249:242]=dst;v[257:250]=epoch;v[259:258]=kind;packet={8'h01,crc32(v),v};end
endfunction
initial begin #2;if(crc32(280'h2221201f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100)!==32'h11e084b7 || crc32(280'b0)!==32'h09f85737)$fatal(1,"CRC_KNOWN_VECTOR_FAIL");end
// Parallel behavioural switch: one independently flow-controlled port pair
// per endpoint. Arbitration never reads the expected/golden layer arrays.
function automatic integer origin(input integer i);
 origin=i*(N/GRID)+(i<N%GRID?i:N%GRID);
endfunction
function automatic integer span(input integer i);
 span=N/GRID+(i<N%GRID);
endfunction
function automatic integer owner_axis(input integer c);
 owner_axis=c<((N/GRID)+1)*(N%GRID)?c/((N/GRID)+1):(N%GRID)+(c-((N/GRID)+1)*(N%GRID))/(N/GRID);
endfunction
function automatic integer xlo(input integer b);xlo=origin(b%GRID)<5?0:origin(b%GRID)-5;endfunction
function automatic integer ylo(input integer b);ylo=origin(b/GRID)<5?0:origin(b/GRID)-5;endfunction
function automatic integer xhi(input integer b);xhi=origin(b%GRID)+span(b%GRID)+5>N?N:origin(b%GRID)+span(b%GRID)+5;endfunction
function automatic integer yhi(input integer b);yhi=origin(b/GRID)+span(b/GRID)+5>N?N:origin(b/GRID)+span(b/GRID)+5;endfunction
function automatic integer needs(input integer dest,x,y);
 needs=x>=xlo(dest)&&x<xhi(dest)&&y>=ylo(dest)&&y<yhi(dest);
endfunction
integer host_pos[0:B-1],round_robin[0:B-1],chosen[0:B-1],locked_source[0:B-1];
integer host_sent=0,host_duplicate=0,skip_done=0,cycles=0;
integer forwarded=0,returned=0,final_count=0,host_stalls=0,route_stalls=0;
reg [B-1:0] held_valid_vec=0;reg [319:0] held_packet[0:B-1];
reg [B-1:0] pending_src[0:B-1];
wire held_valid=|held_valid_vec;wire [B-1:0] pending=held_valid_vec;
reg final_seen[0:N*N-1];reg producer_seen[0:2][0:N*N-1];
reg [191:0] produced[0:1][0:N*N-1];
reg [B-1:0] sampled_in,sampled_out,sampled_valid;
reg [B-1:0] network_previous_stall=0;
reg [319:0] network_held[0:B-1];
integer sampled_chosen[0:B-1];
reg [319:0] sampled_packet[0:B-1];
integer ix,iy,sz,candidate;
reg [319:0] hp;
always @*begin
 network_valid=0;network_ready=0;hp=0;ix=0;iy=0;sz=0;candidate=0;
 for(integer d=0;d<B;d=d+1)begin
  network_data[d]=0;chosen[d]=-1;
  if(!reset)begin
   sz=(xhi(d)-xlo(d))*(yhi(d)-ylo(d));
   if(host_pos[d]<sz)begin
    ix=xlo(d)+host_pos[d]%(xhi(d)-xlo(d));iy=ylo(d)+host_pos[d]/(xhi(d)-xlo(d));
    if(!(fault==9&&d==0&&!skip_done))begin
     hp=packet(inputs[frame*N*N+iy*N+ix],ix,iy,0,255,d,0);
     if(d==0 && host_pos[d]==0)begin
      case(fault)
       1:hp[280]=~hp[280];
       2:begin hp[257:250]=epoch+1;hp[311:280]=crc32(hp[279:0]);end
       4:begin hp[241:234]=0;hp[311:280]=crc32(hp[279:0]);end
       5:begin hp[249:242]=B;hp[311:280]=crc32(hp[279:0]);end
       6:begin hp[207:192]=N;hp[311:280]=crc32(hp[279:0]);end
       7:begin hp[260]=1;hp[311:280]=crc32(hp[279:0]);end
       8:begin hp[191]=1;hp[311:280]=crc32(hp[279:0]);end
       default:;
      endcase
     end
     network_valid[d]=1;network_data[d]=hp;
    end
   end else begin
    chosen[d]=locked_source[d];
    for(integer j=0;j<B;j=j+1)begin
     candidate=(round_robin[d]+j)%B;
     if(chosen[d]<0 && held_valid_vec[candidate] && pending_src[candidate][d])chosen[d]=candidate;
    end
    if(chosen[d]>=0)begin
     hp=held_packet[chosen[d]];
     network_valid[d]=1;network_data[d]=packet(hp[191:0],hp[207:192],hp[223:208],hp[233:232],hp[241:234],d,0);
    end
   end
   if(!held_valid_vec[d] && (scenario!=2 || (cycles+d*3)%19<11))network_ready[d]=1;
   // Test-only corruption proves that the ingress stability assertion fires.
   if(mutate_network && network_previous_stall[d])network_data[d][0]=~network_data[d][0];
  end
 end
end
always @(posedge fabric_clk)begin
 sampled_in<=network_valid&network_accept;sampled_out<=network_ready&network_output;sampled_valid<=network_valid;
 for(integer d=0;d<B;d=d+1)begin
  sampled_chosen[d]<=chosen[d];sampled_packet[d]<=network_return[d];
  if(reset)network_previous_stall[d]<=0;
  else begin
   if(network_previous_stall[d] && (!network_valid[d] || network_data[d]!==network_held[d]))$fatal(1,"NETWORK_STABILITY_FAIL board=%0d",d);
   network_previous_stall[d]<=network_valid[d]&&!network_accept[d];network_held[d]<=network_data[d];
   if(network_valid[d]&&network_accept[d])$fwrite(trace_fd,"%s,%0d,%0d,%0d,%0t,%080h\n",network_data[d][233:232]==0?"HOST":"FORWARD",frame,epoch,d,$time,network_data[d]);
   if(network_ready[d]&&network_output[d])$fwrite(trace_fd,"RETURN,%0d,%0d,%0d,%0t,%080h\n",frame,epoch,d,$time,network_return[d]);
  end
 end
end
always @(negedge fabric_clk)begin:router
 integer p,px,py,layer,src,sz;reg [319:0] v;
 if(reset)begin
  host_sent=0;host_duplicate=0;skip_done=0;cycles=0;held_valid_vec=0;
  forwarded=0;returned=0;final_count=0;host_stalls=0;route_stalls=0;
  for(integer d=0;d<B;d=d+1)begin host_pos[d]=0;round_robin[d]=0;locked_source[d]=-1;pending_src[d]=0;held_packet[d]=0;end
  for(integer k=0;k<N*N;k=k+1)begin final_seen[k]=0;for(integer z=0;z<3;z=z+1)producer_seen[z][k]=0;end
 end else begin
  cycles=cycles+1;
  for(integer d=0;d<B;d=d+1)begin
   sz=(xhi(d)-xlo(d))*(yhi(d)-ylo(d));
   if(sampled_in[d])locked_source[d]=-1;
   else if(sampled_valid[d] && sampled_chosen[d]>=0)locked_source[d]=sampled_chosen[d];
   if(host_pos[d]<sz)begin
    if(fault==9&&d==0&&!skip_done)begin skip_done=1;host_pos[d]=host_pos[d]+1;end
    else if(sampled_in[d])begin
     host_sent=host_sent+1;
     if(fault==3&&d==0&&!host_duplicate)host_duplicate=1;else host_pos[d]=host_pos[d]+1;
    end else host_stalls=host_stalls+1;
   end else if(sampled_in[d])begin
    if(sampled_chosen[d]<0)$fatal(1,"ROUTE_SELECTION_FAIL");
    pending_src[sampled_chosen[d]][d]=0;round_robin[d]=(sampled_chosen[d]+1)%B;forwarded=forwarded+1;
   end else if(network_valid[d])route_stalls=route_stalls+1;
  end
  for(integer s=0;s<B;s=s+1)if(held_valid_vec[s] && pending_src[s]==0)held_valid_vec[s]=0;
  for(integer s=0;s<B;s=s+1)if(sampled_out[s])begin
   v=sampled_packet[s];px=v[207:192];py=v[223:208];layer=v[233:232];src=v[241:234];p=py*N+px;
   if(v[319:312]!=1 || v[311:280]!==crc32(v[279:0]) || v[279:260]!=0 || v[257:250]!=epoch || v[231:224]!=frame || v[249:242]!=255 || src!=s || px>=N || py>=N || src!=owner_axis(py)*GRID+owner_axis(px) || layer<1 || layer>3)$fatal(1,"RETURN_PACKET_FAIL");
   if(producer_seen[layer-1][p])$fatal(1,"RETURN_DUPLICATE");producer_seen[layer-1][p]=1;returned=returned+1;
   if(layer==3)begin
    if(v[259:258]!=2 || v[191:0]!==expected3[frame*N*N+p] || final_seen[p])$fatal(1,"FINAL_PACKET_FAIL");
    final_seen[p]=1;final_count=final_count+1;
   end else begin
    if(v[259:258]!=0 || held_valid_vec[s])$fatal(1,"INTERMEDIATE_KIND_FAIL");
    produced[layer-1][p]=v[191:0];held_packet[s]=v;pending_src[s]=0;
    for(integer d=0;d<B;d=d+1)if(needs(d,px,py))pending_src[s][d]=1;
    held_valid_vec[s]=(pending_src[s]!=0);
   end
  end
 end
end
// Independent load-time provenance: later-layer data must be exactly the
// actual producer packet observed above, never a golden-array injection.
for(genvar pv=0;pv<B;pv=pv+1)begin:provenance
 integer p,l;
 always @(posedge bclk[pv])if(!reset&&rx_valid[pv]&&rx_ready[pv])begin
  l=rx_data[pv][233:232];p=rx_data[pv][223:208]*N+rx_data[pv][207:192];
  if(l>0&&l<3&&p<N*N)begin
   if(!producer_seen[l-1][p] || rx_data[pv][191:0]!==produced[l-1][p])$fatal(1,"PROVENANCE_FAIL");
  end
 end
end
integer aborted=0,total_sites,expected_count,timeout,expected_error,drain_wait;time started;
reg terminal_fault=0;
initial begin
 #2;
 for(frame=0;frame<F&&!terminal_fault;frame=frame+1)begin
  reset=1;epoch=epoch+1;repeat(20)@(negedge fabric_clk);reset=0;started=$time;timeout=0;
  while(final_count<N*N && timeout<2000000 && !terminal_fault)begin
   @(negedge fabric_clk);timeout=timeout+1;
   if(abort_test&&!aborted&&boards[0].dut.state==3&&boards[0].dut.clocks>25)begin
    $display("RESET_ABORT epoch=%0d elapsed_ns=%0t",epoch,$time-started);
    reset=1;epoch=epoch+1;repeat(20)@(negedge fabric_clk);reset=0;started=$time;timeout=0;aborted=1;
   end
   if(|failed)begin
    case(fault)1:expected_error=2;2:expected_error=3;3:expected_error=7;4:expected_error=6;5:expected_error=4;6:expected_error=5;7:expected_error=1;8:expected_error=8;default:expected_error=0;endcase
    if(failed[0]&&errors[0]==expected_error&&expected_error!=0)begin terminal_fault=1;$display("FAULT_PASS fault=%0d error=%0d final_count=%0d",fault,errors[0],final_count);$finish;end
    else $fatal(1,"ENDPOINT_FAIL fault=%0d code0=%0d",fault,errors[0]);
   end
  end
  if(fault==9 && timeout==2000000 && final_count<N*N && !(&finished))begin
   if(boards[0].dut.state!=1 || boards[0].dut.phase!=0 || boards[0].dut.received[0]!=boards[0].dut.REQUIRED-1 || boards[0].dut.seen[0][5*M+5]!==0)$fatal(1,"MISSING_RECORD_WRONG_FAILURE");
   terminal_fault=1;$display("FAULT_PASS fault=9 reason=missing_record_blocks_completion final_count=%0d",final_count);$finish;
  end
  if(!terminal_fault)begin
  if(fault!=0 || timeout>=2000000)$fatal(1,"COMPLETION_FAIL");
  drain_wait=0;
  while((!(&finished) || held_valid || pending!=0 || network_output!=0 || network_valid!=0) && drain_wait<8*(T*T+16))begin
   @(negedge fabric_clk);drain_wait=drain_wait+1;
  end
  if(drain_wait>=8*(T*T+16))$fatal(1,"DRAIN_SETTLE_TIMEOUT finished=%h",finished);
  $display("DRAIN_SETTLE frame=%0d cycles=%0d bound=%0d",frame,drain_wait,8*(T*T+16));
  repeat(40)@(negedge fabric_clk);
  if(!(&finished) || held_valid || pending!=0 || network_output!=0 || network_valid!=0 || returned!=3*N*N)$fatal(1,"DRAIN_FAIL");
  total_sites=0;
  for(integer b=0;b<B;b=b+1)begin
   expected_count=0;
   for(integer y=0;y<N;y=y+1)for(integer x=0;x<N;x=x+1)if(needs(b,x,y))expected_count=expected_count+1;
   if(accepted[b]!=3*expected_count)$fatal(1,"ACCEPT_COUNT_FAIL");
   for(integer l=0;l<3;l=l+1)begin
    if(allcounts[b][l]!=M*M)$fatal(1,"LOCAL_OUTPUT_COUNT_FAIL");total_sites=total_sites+counts[b][l];
   end
   $display("BOARD_PASS frame=%0d board=%0d accepted=%0d emitted=%0d clocks=%0d,%0d,%0d",frame,b,accepted[b],emitted[b],c0[b],c1[b],c2[b]);
  end
  if(total_sites!=3*N*N || (abort_test&&!aborted))$fatal(1,"OWNERSHIP_COUNT_FAIL");
  $display("MULTIBOARD_PASS n=%0d grid=%0d tile=%0d frame=%0d scenario=%0d epoch=%0d elapsed_ns=%0t host=%0d forwarded=%0d returned=%0d final=%0d raw_channels=%0d host_stalls=%0d route_stalls=%0d",N,GRID,T,frame,scenario,epoch,$time-started,host_sent,forwarded,returned,final_count,20*N*N,host_stalls,route_stalls);
  end
 end
 if(!terminal_fault)begin $display("SUITE_PASS frames=%0d scenario=%0d abort=%0d",F,scenario,aborted);$finish;end
end
endmodule
