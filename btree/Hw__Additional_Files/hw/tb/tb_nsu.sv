// =============================================================================
// tb_nsu.sv — self-checking testbench for the Node Search Unit (nsu_top, streaming early-out)
//
// Vectors: hw/vectors/nsu_vectors.hex (hw/gen_vectors.py), 34 x 64-bit values per
// vector: count, query, keys[0..30], expected (bit 8 found, bits 4:0 position);
// keys and query fit in 32 bits. Memory model: node block of vector v at v*128:
// entry 0 = count, entries 1..31 = keys, 32-bit entries, 16 per 64 B line;
// 3-cycle latency, ready stalled 1 cycle in 4. Issues one nsrch per vector to
// nsu_top, checks rd, and records per instruction: cycles issue -> rd_valid,
// cycles until the unit is free again (busy low), and the number of line
// requests issued.
// =============================================================================
`timescale 1ns/1ps
module tb_nsu;
  localparam int WPV = 34, MAXV = 4096, MEM_LAT = 3;

  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  logic [63:0] vec [0:MAXV*WPV-1];
  int nv = 0;
  function automatic logic [63:0] V(input int v, input int w); return vec[v*WPV + w]; endfunction
  function automatic int   exp_pos  (input int v); return int'(V(v,33)[4:0]); endfunction
  function automatic logic exp_found(input int v); return V(v,33)[8];         endfunction

  logic         issue_valid, busy, mem_req_valid, mem_req_ready, mem_rsp_valid, rd_valid;
  logic [63:0]  rs1_addr, rs2_query, mem_req_addr, rd_data;
  logic [511:0] mem_rsp_data;

  nsu_top dut (
    .clk(clk), .rst_n(rst_n),
    .issue_valid(issue_valid), .rs1_addr(rs1_addr), .rs2_query(rs2_query), .busy(busy),
    .mem_req_valid(mem_req_valid), .mem_req_ready(mem_req_ready), .mem_req_addr(mem_req_addr),
    .mem_rsp_valid(mem_rsp_valid), .mem_rsp_data(mem_rsp_data),
    .rd_valid(rd_valid), .rd_data(rd_data));

  // node block of vector v at byte address v*128: 32-bit entries, line j = entries 16j..16j+15
  function automatic logic [511:0] line_of(input logic [63:0] addr);
    logic [511:0] l; int v, j, w;
    v = int'(addr >> 7); j = int'(addr[6]); l = '0;
    for (int k = 0; k < 16; k++) begin
      w = j*16 + k;
      if (w == 0)       l[k*32 +: 32] = V(v, 0)[31:0];          // count
      else if (w <= 31) l[k*32 +: 32] = V(v, 2 + w - 1)[31:0];  // key[w-1]
    end
    return l;
  endfunction

  logic        pend_v [0:MEM_LAT-1];
  logic [63:0] pend_a [0:MEM_LAT-1];
  int cyc = 0, req_count = 0;
  assign mem_req_ready = cyc[1] | cyc[0];
  always_ff @(posedge clk) begin
    cyc <= cyc + 1;
    if (mem_req_valid & mem_req_ready) req_count <= req_count + 1;
    pend_v[0] <= mem_req_valid & mem_req_ready;
    pend_a[0] <= mem_req_addr;
    for (int i = 1; i < MEM_LAT; i++) begin pend_v[i] <= pend_v[i-1]; pend_a[i] <= pend_a[i-1]; end
    mem_rsp_valid <= pend_v[MEM_LAT-1];
    mem_rsp_data  <= line_of(pend_a[MEM_LAT-1]);
  end

  int errors = 0, tot_lat = 0, min_lat = 1 << 30, max_lat = 0, tot_busy = 0, tot_lines = 0;
  int lat_hist [0:31];
  task automatic run_instr(input int v);
    int c0, c1, c2, r0;
    @(negedge clk);
    while (busy) @(negedge clk);
    issue_valid = 1; rs1_addr = 64'(v) * 128; rs2_query = V(v,1);
    c0 = cyc; r0 = req_count;
    @(negedge clk); issue_valid = 0;
    while (!rd_valid) @(negedge clk);
    c1 = cyc;
    if (int'(rd_data[4:0]) != exp_pos(v) || rd_data[8] != exp_found(v) || rd_data[63:9] != '0 || rd_data[7:5] != '0) begin
      errors++;
      if (errors <= 10)
        $display("FAIL v=%0d count=%0d query=%h : rd=%h, expected pos=%0d found=%0d", v, V(v,0), V(v,1), rd_data, exp_pos(v), exp_found(v));
    end
    while (busy) @(negedge clk);
    c2 = cyc;
    tot_lat += (c1 - c0); tot_busy += (c2 - c0); tot_lines += (req_count - r0);
    if (c1 - c0 > max_lat) max_lat = c1 - c0;
    if (c1 - c0 < min_lat) min_lat = c1 - c0;
    if (c1 - c0 < 32) lat_hist[c1 - c0]++;
  endtask

  initial begin
    issue_valid = 0; rs1_addr = 0; rs2_query = 0;
    for (int i = 0; i < MEM_LAT; i++) begin pend_v[i] = 0; pend_a[i] = 0; end
    for (int i = 0; i < 32; i++) lat_hist[i] = 0;
    mem_rsp_valid = 0; mem_rsp_data = '0;
    if (!$value$plusargs("NV=%d", nv)) nv = 100;
    $readmemh("hw/vectors/nsu_vectors.hex", vec);
    $display("tb_nsu: %0d vectors", nv);
    repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
    for (int v = 0; v < nv; v++) run_instr(v);
    $display("NSU (streaming early-out, 32-bit keys, 2-line block; issue -> rd_valid, mem latency %0d): %0d vectors, %0d errors; cycles min %0d avg %.1f max %0d; busy avg %.1f cycles; lines requested avg %.2f",
             MEM_LAT, nv, errors, min_lat, real'(tot_lat) / nv, max_lat, real'(tot_busy) / nv, real'(tot_lines) / nv);
    $write("latency histogram:");
    for (int i = 0; i < 32; i++) if (lat_hist[i]) $write("  %0d cyc: %0d", i, lat_hist[i]);
    $display("");
    if (errors == 0) $display("PASS: all %0d vectors correct", nv);
    else $display("FAIL: %0d errors", errors);
    $finish;
  end
endmodule
