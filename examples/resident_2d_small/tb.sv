`timescale 1ns/1ps
module tb;
localparam N=9,V=2,CW=$clog2(N),F=6,SCANS=190;
reg clk=0;always #5 clk=~clk;
reg reset=1,start=0,frame_slot=0;
reg [V-1:0] load_valid=0;reg [V*CW-1:0] load_x=0,load_y=0;reg [V*4-1:0] load_data=0;
wire load_ready,busy,done;wire [31:0] elapsed_cycles;
wire [V-1:0] result_valid;wire [V*CW-1:0] result_x,result_y;wire [V*2-1:0] result_data;
reg result_read_enable=0,result_read_slot=0;reg [CW-1:0] result_read_x=0,result_read_y=0;
wire result_read_valid;wire [1:0] result_read_data;
integer frame=0,cycles=0,start_cycle=0,end_cycle=0,events,fi,l,at,first_latency=-1;
integer count1=0,count2=0,count3=0,issued1=0,issued2=0,issued3=0;
integer stalls1=0,stalls2=0,stalls3=0;
reg checking=0;
wire [2:0] issue_enable=(!checking || frame<3)?3'b111:{(cycles%13<10),(cycles%11<9),(cycles%7!=0)};
worker_rowready #(.N(N),.V(V)) dut(.*);
reg [3:0] inputs[0:F*N*N-1];
reg [143:0] raw1[0:F*N*N-1];reg [98:0] act1[0:F*N*N-1];
wire [V*144-1:0] total1;
for(genvar g1=0;g1<V;g1=g1+1)begin
 assign total1[g1*144+:144]=dut.l1.arithmetic[g1].total_observable_in_simulation;
end
reg [215:0] raw2[0:F*N*N-1];reg [188:0] act2[0:F*N*N-1];
wire [V*216-1:0] total2;
for(genvar g2=0;g2<V;g2=g2+1)begin
 assign total2[g2*216+:216]=dut.l2.arithmetic[g2].total_observable_in_simulation;
end
reg [71:0] raw3[0:F*N*N-1];reg [1:0] act3[0:F*N*N-1];
wire [V*72-1:0] total3;
for(genvar g3=0;g3<V;g3=g3+1)begin
 assign total3[g3*72+:72]=dut.l3.arithmetic[g3].total_observable_in_simulation;
end
always @(posedge clk) cycles<=cycles+1;
initial begin events=$fopen("events.csv","w");$fwrite(events,"frame,layer,cycle,index\n");end
always @(posedge clk) begin
 if(start && !busy)start_cycle=cycles;
 if(checking && !reset && busy)begin
  if(dut.l2.feeder.issue)for(integer lane=0;lane<V;lane=lane+1)
   if(dut.l2.feeder.map_present[lane] && (dut.l2.feeder.scan_y-5)*N+dut.l2.feeder.scan_word*V+lane-5>=count1)
    $fatal(1,"READ_BEFORE_COMMIT layer=2");
  if(dut.l3.feeder.issue)for(integer lane=0;lane<V;lane=lane+1)
   if(dut.l3.feeder.map_present[lane] && (dut.l3.feeder.scan_y-5)*N+dut.l3.feeder.scan_word*V+lane-5>=count2)
    $fatal(1,"READ_BEFORE_COMMIT layer=3");
  if(dut.l1.feeder.issue)issued1=issued1+1;
  if(dut.l1.feeder.scanning && !dut.l1.feeder.issue)stalls1=stalls1+1;
  for(integer lane=0;lane<V;lane=lane+1)if(dut.v1[lane])begin
   integer p;
   if(dut.x1[lane*CW+:CW]>=N || dut.y1[lane*CW+:CW]>=N)$fatal(1,"COORD_BOUNDS");
   p=dut.y1[lane*CW+:CW]*N+dut.x1[lane*CW+:CW];
   if(p!==count1)$fatal(1,"OUTPUT_ORDER layer=1 p=%0d count=%0d",p,count1);
   if(total1[lane*144+:144]!==raw1[frame*N*N+p])$fatal(1,"RAW_MISMATCH layer=1 frame=%0d p=%0d",frame,p);
   if(dut.a1[lane*99+:99]!==act1[frame*N*N+p])$fatal(1,"ACT_MISMATCH layer=1 frame=%0d p=%0d",frame,p);
   count1=count1+1;
   $fwrite(events,"%0d,1,%0d,%0d\n",frame,cycles-start_cycle,p);
  end
  if(dut.l2.feeder.issue)issued2=issued2+1;
  if(dut.l2.feeder.scanning && !dut.l2.feeder.issue)stalls2=stalls2+1;
  for(integer lane=0;lane<V;lane=lane+1)if(dut.v2[lane])begin
   integer p;
   if(dut.x2[lane*CW+:CW]>=N || dut.y2[lane*CW+:CW]>=N)$fatal(1,"COORD_BOUNDS");
   p=dut.y2[lane*CW+:CW]*N+dut.x2[lane*CW+:CW];
   if(p!==count2)$fatal(1,"OUTPUT_ORDER layer=2 p=%0d count=%0d",p,count2);
   if(total2[lane*216+:216]!==raw2[frame*N*N+p])$fatal(1,"RAW_MISMATCH layer=2 frame=%0d p=%0d",frame,p);
   if(dut.a2[lane*189+:189]!==act2[frame*N*N+p])$fatal(1,"ACT_MISMATCH layer=2 frame=%0d p=%0d",frame,p);
   count2=count2+1;
   $fwrite(events,"%0d,2,%0d,%0d\n",frame,cycles-start_cycle,p);
  end
  if(dut.l3.feeder.issue)issued3=issued3+1;
  if(dut.l3.feeder.scanning && !dut.l3.feeder.issue)stalls3=stalls3+1;
  for(integer lane=0;lane<V;lane=lane+1)if(dut.v3[lane])begin
   integer p;
   if(dut.x3[lane*CW+:CW]>=N || dut.y3[lane*CW+:CW]>=N)$fatal(1,"COORD_BOUNDS");
   p=dut.y3[lane*CW+:CW]*N+dut.x3[lane*CW+:CW];
   if(p!==count3)$fatal(1,"OUTPUT_ORDER layer=3 p=%0d count=%0d",p,count3);
   if(total3[lane*72+:72]!==raw3[frame*N*N+p])$fatal(1,"RAW_MISMATCH layer=3 frame=%0d p=%0d",frame,p);
   if(dut.a3[lane*2+:2]!==act3[frame*N*N+p])$fatal(1,"ACT_MISMATCH layer=3 frame=%0d p=%0d",frame,p);
   count3=count3+1;
   $fwrite(events,"%0d,3,%0d,%0d\n",frame,cycles-start_cycle,p);
   if(p==N*N-1)end_cycle=cycles;
  end
 end
end
task load_frame(input integer f);
 begin
  if(!load_ready || busy)$fatal(1,"LOAD_BUSY");
  @(negedge clk);frame_slot=f%2;
  for(integer y=0;y<N;y=y+1)for(integer x=0;x<N;x=x+V)begin
   @(negedge clk);load_valid=0;
   for(integer lane=0;lane<V;lane=lane+1)if(x+lane<N)begin
    load_valid[lane]=1;load_x[lane*CW+:CW]=x+lane;load_y[lane*CW+:CW]=y;
    load_data[lane*4+:4]=inputs[f*N*N+y*N+x+lane];
   end
  end
  @(negedge clk);load_valid=0;
 end
endtask
task read_frame(input integer f);
 begin
  for(integer p=0;p<N*N;p=p+1)begin
   result_read_enable=1;result_read_slot=f%2;result_read_x=p%N;result_read_y=p/N;
   @(negedge clk);
   if(!result_read_valid || result_read_data!==act3[f*N*N+p])$fatal(1,"READBACK_MISMATCH frame=%0d p=%0d",f,p);
  end
  result_read_enable=0;
 end
endtask
initial begin
 $readmemh("inputs.hex",inputs);
 $readmemh("raw1.hex",raw1);$readmemh("act1.hex",act1);
 $readmemh("raw2.hex",raw2);$readmemh("act2.hex",act2);
 $readmemh("raw3.hex",raw3);$readmemh("act3.hex",act3);
 repeat(5)@(negedge clk);reset=0;
 // Interrupt after all three layers have produced work, then reload cleanly.
 load_frame(0);@(negedge clk);start=1;@(negedge clk);start=0;
 wait(|result_valid);@(negedge clk);reset=1;repeat(3)@(negedge clk);reset=0;
 $display("ACTIVE_PIPELINE_RESET_RECOVERY");
 for(frame=0;frame<F;frame=frame+1)begin
  checking=0;load_frame(frame);
  count1=0;count2=0;count3=0;issued1=0;issued2=0;issued3=0;stalls1=0;stalls2=0;stalls3=0;
  @(negedge clk);start=1;checking=1;@(negedge clk);start=0;
  wait(done);@(negedge clk);checking=0;
  if(count1!=N*N || count2!=N*N || count3!=N*N)$fatal(1,"OUTPUT_COUNT");
  if(issued1!=SCANS || issued2!=SCANS || issued3!=SCANS)$fatal(1,"ISSUE_COUNT %0d %0d %0d",issued1,issued2,issued3);
  if(elapsed_cycles!=end_cycle-start_cycle)$fatal(1,"LATENCY_ENDPOINT");
  if(frame==0)first_latency=elapsed_cycles;
  if(frame<3 && elapsed_cycles!=first_latency)$fatal(1,"DATA_DEPENDENT_SCHEDULE");
  read_frame(frame);if(frame>0)read_frame(frame-1);
  $display("FRAME_PASS n=%0d v=%0d frame=%0d cycles=%0d values=%0d stalls=%0d,%0d,%0d start=%0d",N,V,frame,elapsed_cycles,40*N*N,stalls1,stalls2,stalls3,start_cycle);
 end
 $display("ALL_PASS");$fclose(events);$finish;
end
initial begin repeat(124051)@(posedge clk);$fatal(1,"WATCHDOG");end
endmodule
