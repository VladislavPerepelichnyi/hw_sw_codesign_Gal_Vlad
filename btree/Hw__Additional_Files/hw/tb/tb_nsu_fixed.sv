// =============================================================================
// tb_nsu_fixed.sv — self-checking testbench for the fixed 31-lane design (nsu_top_fixed + nsu_core)
//
// Vectors: hw/vectors/nsu_vectors.hex (hw/gen_vectors.py): real nodes and
// queries recorded from the pyperformance btree benchmark plus corner cases.
// 34 x 64-bit values per vector: count, query, keys[0..30], expected
// (bit 8 found, bits 4:0 position); keys and query fit in 32 bits.
//
// Phase A  nsu_top_fixed as an instruction: issue (rs1 = node block address,
//          rs2 = query), wait for rd_valid, check rd. The memory model holds
//          each vector's node block at v*128: entry 0 = count, entries 1..31 =
//          keys (32-bit entries, 2 lines of 64 B), fixed latency, ready stalled 1 cycle in 4.
// Phase B  nsu_core throughput: one search per cycle back-to-back, results
//          checked at the 2-cycle pipeline latency.
//
// Run: hw/run_sim.sh   (Verilator --binary --timing), +NV=<vectors> optional.
// The testbench is behavioural (loops, ints, queues); only hw/rtl/ is hardware.
// =============================================================================
`timescale 1ns/1ps
module tb_nsu_fixed;
  localparam int WPV = 34, MAXV = 4096, MEM_LAT = 3;

  logic clk = 0, rst_n = 0;
  always #5 clk = ~clk;

  // ------------------------------------------------------------ vectors
  logic [63:0] vec [0:MAXV*WPV-1];
  int nv = 0;
  function automatic logic [63:0] V(input int v, input int w); return vec[v*WPV + w]; endfunction
  function automatic int   exp_pos  (input int v); return int'(V(v,33)[4:0]); endfunction
  function automatic logic exp_found(input int v); return V(v,33)[8];         endfunction

  // ------------------------------------------------------------ DUT
  logic         issue_valid, busy, mem_req_valid, mem_req_ready, mem_rsp_valid, rd_valid;
  logic [63:0]  rs1_addr, rs2_query, mem_req_addr, rd_data;
  logic [511:0] mem_rsp_data;

  nsu_top_fixed dut (
    .clk(clk), .rst_n(rst_n),
    .issue_valid(issue_valid), .rs1_addr(rs1_addr), .rs2_query(rs2_query), .busy(busy),
    .mem_req_valid(mem_req_valid), .mem_req_ready(mem_req_ready), .mem_req_addr(mem_req_addr),
    .mem_rsp_valid(mem_rsp_valid), .mem_rsp_data(mem_rsp_data),
    .rd_valid(rd_valid), .rd_data(rd_data));

  // ------------------------------------------------------------ memory model
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
  int cyc = 0;
  assign mem_req_ready = cyc[1] | cyc[0];        // ready 3 cycles of 4
  always_ff @(posedge clk) begin
    cyc <= cyc + 1;
    pend_v[0] <= mem_req_valid & mem_req_ready;
    pend_a[0] <= mem_req_addr;
    for (int i = 1; i < MEM_LAT; i++) begin pend_v[i] <= pend_v[i-1]; pend_a[i] <= pend_a[i-1]; end
    mem_rsp_valid <= pend_v[MEM_LAT-1];
    mem_rsp_data  <= line_of(pend_a[MEM_LAT-1]);
  end

  // ------------------------------------------------------------ phase A: one instruction per vector
  int errors = 0, tot_cycles = 0, max_cycles = 0, min_cycles = 1 << 30;
  task automatic run_instr(input int v);
    int c0, c1;
    @(negedge clk);
    while (busy) @(negedge clk);
    issue_valid = 1; rs1_addr = 64'(v) * 128; rs2_query = V(v,1);
    c0 = cyc;
    @(negedge clk); issue_valid = 0;
    while (!rd_valid) @(negedge clk);
    c1 = cyc;
    if (int'(rd_data[4:0]) != exp_pos(v) || rd_data[8] != exp_found(v) || rd_data[63:9] != '0 || rd_data[7:5] != '0) begin
      errors++;
      if (errors <= 10)
        $display("FAIL instr v=%0d count=%0d query=%h : rd=%h, expected pos=%0d found=%0d",
                 v, V(v,0), V(v,1), rd_data, exp_pos(v), exp_found(v));
    end
    tot_cycles += (c1 - c0);
    if (c1 - c0 > max_cycles) max_cycles = c1 - c0;
    if (c1 - c0 < min_cycles) min_cycles = c1 - c0;
  endtask

  // ------------------------------------------------------------ phase B: core throughput
  logic              s_in_valid, s_out_valid, s_out_found;
  logic [31:0]       s_query;
  logic [30:0][31:0] s_keys;
  logic [4:0]        s_count, s_out_pos;
  nsu_core core (
    .clk(clk), .rst_n(rst_n), .in_valid(s_in_valid), .in_query(s_query), .in_keys(s_keys),
    .in_count(s_count), .out_valid(s_out_valid), .out_position(s_out_pos), .out_found(s_out_found));

  int s_errors = 0, s_checked = 0;
  int s_exp_q[$];
  always_ff @(posedge clk) begin
    if (s_out_valid) begin
      int v;
      if (s_exp_q.size() == 0) s_errors++;
      else begin
        v = s_exp_q.pop_front(); s_checked++;
        if (int'(s_out_pos) != exp_pos(v) || s_out_found != exp_found(v)) begin
          s_errors++;
          if (s_errors <= 10) $display("FAIL core v=%0d: got pos=%0d found=%0d expected pos=%0d found=%0d",
                                       v, s_out_pos, s_out_found, exp_pos(v), exp_found(v));
        end
      end
    end
  end

  // ------------------------------------------------------------ main
  initial begin
    issue_valid = 0; rs1_addr = 0; rs2_query = 0;
    s_in_valid = 0; s_query = 0; s_keys = '0; s_count = 0;
    for (int i = 0; i < MEM_LAT; i++) begin pend_v[i] = 0; pend_a[i] = 0; end
    mem_rsp_valid = 0; mem_rsp_data = '0;

    if (!$value$plusargs("NV=%d", nv)) nv = 100;
    $readmemh("hw/vectors/nsu_vectors.hex", vec);
    $display("tb_nsu_fixed: %0d vectors", nv);
    repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);

    for (int v = 0; v < nv; v++) run_instr(v);
    $display("phase A (fixed 31-lane unit, 32-bit keys, nsrch: issue -> rd_valid, mem latency %0d, 2 lines): %0d vectors, %0d errors; cycles min %0d avg %0d max %0d",
             MEM_LAT, nv, errors, min_cycles, tot_cycles / nv, max_cycles);

    for (int v = 0; v < nv; v++) begin
      @(negedge clk);
      s_in_valid = 1; s_query = V(v,1)[31:0]; s_count = V(v,0)[4:0];
      for (int i = 0; i < 31; i++) s_keys[i] = V(v, 2 + i)[31:0];
      s_exp_q.push_back(v);
    end
    @(negedge clk); s_in_valid = 0;
    repeat (4) @(posedge clk);
    $display("phase B (nsu_core throughput, 1 search/cycle, latency 2): %0d checked, %0d errors", s_checked, s_errors);
    if (s_checked != nv) begin s_errors++; $display("FAIL: expected %0d results, saw %0d", nv, s_checked); end

    if (errors == 0 && s_errors == 0) $display("PASS: all %0d vectors correct in both phases", nv);
    else $display("FAIL: %0d instruction errors, %0d core errors", errors, s_errors);
    $finish;
  end
endmodule
