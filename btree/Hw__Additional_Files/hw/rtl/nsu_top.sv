// =============================================================================
// nsu_top.sv — the Node Search Unit (NSU): execution unit behind one instruction,
//              streaming early-out design, 32-bit keys
//
//     nsrch rd, rs1, rs2      rd = { found, position }   (bit 8, bits 4:0)
//
//     node block (rs1): 32 x 32-bit entries = 128 B, 128 B aligned = 2 cache lines;
//     entry 0 = key count (0..31), entries 1..31 = keys k0..k30, sorted, unsigned.
//     Line 0 carries entries 0..15 (header + k0..k14), line 1 entries 16..31 (k15..k30).
//     rs2: the query key in bits 31:0 (keys outside 0..2^32-1 take the software path).
//     (The fixed 31-lane design this evolved from is nsu_top_fixed.sv + nsu_core.sv.)
//
// The node lines are compared AS THEY ARRIVE, 16 keys per line, and the search
// stops at the first line that contains a key >= query (the keys are sorted, so
// every later key is also >= query) or at the last line that holds keys. A running
// count of keys < query is the position.
//
//   line j arrives --> 16 lanes: lt[k] = valid & key < q, eq[k] = valid & key == q
//                  --> S1 registers lt[15:0], eq[15:0], boundary = |(valid & ~lt), last
//                  --> S2: pos += popcount16(lt); found |= |eq;
//                          boundary | last  ->  rd = {found, pos}, rd_valid
//   a line that arrives after the result is drained and ignored; no further line
//   request is issued once the result is known.
//
// Structure (top to bottom):
//   1. Control        operand registers, FSM, line requests, response counting
//   2. Line compare   16 lanes on the arriving line + validity mask (S1 register)
//   3. Accumulate     popcount-16 adder tree, running position / found (S2)
//   4. rd register    {found, position} + rd_valid for writeback
// =============================================================================
module nsu_top (
  input  logic         clk,
  input  logic         rst_n,

  // issue from the execute stage
  input  logic         issue_valid,
  input  logic [63:0]  rs1_addr,          // node block address, 128 B aligned
  input  logic [63:0]  rs2_query,         // query key in bits 31:0
  output logic         busy,

  // L1 D-cache line port, read only
  output logic         mem_req_valid,
  input  logic         mem_req_ready,
  output logic [63:0]  mem_req_addr,
  input  logic         mem_rsp_valid,
  input  logic [511:0] mem_rsp_data,      // one 64 B line = 16 entries

  // writeback
  output logic         rd_valid,
  output logic [63:0]  rd_data            // {55'b0, found, 3'b0, position[4:0]}
);

  // =========================================================== 1. Control
  typedef enum logic [1:0] { S_IDLE, S_RUN } state_t;
  state_t      state;
  logic [63:0] addr_q;                     // operand registers
  logic [31:0] query_q;
  logic [1:0]  req_idx, rsp_idx;           // lines requested / received, 0..2
  logic        accept, done_q;             // done_q: result already produced for this instruction
  logic        line_v;                     // a line of this instruction is arriving now

  assign busy          = (state != S_IDLE);
  assign accept        = issue_valid & ~busy;
  assign mem_req_valid = (state == S_RUN) & (req_idx != 2'd2) & ~done_q;   // stop issuing once done
  assign mem_req_addr  = addr_q + {57'd0, req_idx[0], 6'd0};               // rs1 + 64 B * line
  assign line_v        = (state == S_RUN) & mem_rsp_valid;

  // key count: entry 0 of line 0 (the block header). For line 0 it is taken live
  // from the arriving data, afterwards from the register.
  logic [4:0] count_q, count_live;
  assign count_live = (rsp_idx == 2'd0) ? mem_rsp_data[4:0] : count_q;

  // the last line that holds keys: key slot cnt-1 is entry cnt, line = cnt >> 4
  // (cnt = 0 -> line 0, nothing valid, position 0)
  logic last_line;
  assign last_line = (rsp_idx == {1'b0, count_live[4]});

  // S1 valid / S2 valid handshake and drain
  logic v1, v2_fire;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state <= S_IDLE; addr_q <= 64'd0; query_q <= 32'd0;
      req_idx <= 2'd0; rsp_idx <= 2'd0; count_q <= 5'd0; done_q <= 1'b0;
    end else begin
      case (state)
        S_IDLE: begin
          if (accept) begin
            addr_q  <= rs1_addr;  query_q <= rs2_query[31:0];
            req_idx <= 2'd0;      rsp_idx <= 2'd0;
            done_q  <= 1'b0;
            state   <= S_RUN;
          end
        end
        S_RUN: begin
          if (mem_req_valid & mem_req_ready) req_idx <= req_idx + 2'd1;
          if (mem_rsp_valid) begin
            rsp_idx <= rsp_idx + 2'd1;
            if (rsp_idx == 2'd0) count_q <= mem_rsp_data[4:0];
          end
          if (v2_fire) done_q <= 1'b1;                       // result produced this cycle
          // finished when the result is out and every requested line has returned
          if (done_q & (rsp_idx == req_idx) & ~mem_req_valid & ~v1 & ~mem_rsp_valid) state <= S_IDLE;
        end
        default: state <= S_IDLE;
      endcase
    end
  end

  // the upper half of rs2 is architecturally ignored (32-bit keys)
  // verilator lint_off UNUSEDSIGNAL
  logic [31:0] rs2_hi_unused;
  assign rs2_hi_unused = rs2_query[63:32];
  // verilator lint_on UNUSEDSIGNAL

  // =========================================================== 2. Line compare (16 lanes) -> S1
  // entry k of line rsp_idx is key slot (16*rsp_idx + k - 1); entry 0 of line 0 is the key count
  logic [15:0] valid_c, lt_c, eq_c;
  genvar k;
  generate
    for (k = 0; k < 16; k++) begin : lane
      logic [4:0]  entry_idx;                                   // 0..31
      logic [31:0] key;
      assign entry_idx = {rsp_idx[0], 4'(k)};
      assign key       = mem_rsp_data[k*32 +: 32];
      // valid key: entry index >= 1 and slot (entry-1) < count  <=>  entry_idx <= count
      assign valid_c[k] = (entry_idx != 5'd0) & (entry_idx <= count_live);
      assign lt_c[k]    = valid_c[k] & (key <  query_q);
      assign eq_c[k]    = valid_c[k] & (key == query_q);
    end
  endgenerate

  logic [15:0] lt_q, eq_q;
  logic        boundary_q, last_q;
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      v1 <= 1'b0; lt_q <= 16'd0; eq_q <= 16'd0; boundary_q <= 1'b0; last_q <= 1'b0;
    end else begin
      v1         <= line_v & ~done_q;                     // lines after the result are ignored
      lt_q       <= lt_c;
      eq_q       <= eq_c;
      boundary_q <= |(valid_c & ~lt_c);                   // a valid key >= query is in this line
      last_q     <= last_line;
    end
  end

  // =========================================================== 3. Accumulate -> S2
  // popcount of 16 bits: 8 half adders -> 4 adders -> 2 adders -> 1 adder
  logic [1:0] p1 [0:7];
  logic [2:0] p2 [0:3];
  logic [3:0] p3 [0:1];
  logic [4:0] p16;
  generate
    for (k = 0; k < 8; k++) begin : pc1
      assign p1[k] = {1'b0, lt_q[2*k]} + {1'b0, lt_q[2*k+1]};
    end
    for (k = 0; k < 4; k++) begin : pc2
      assign p2[k] = {1'b0, p1[2*k]} + {1'b0, p1[2*k+1]};
    end
    for (k = 0; k < 2; k++) begin : pc3
      assign p3[k] = {1'b0, p2[2*k]} + {1'b0, p2[2*k+1]};
    end
  endgenerate
  assign p16 = {1'b0, p3[0]} + {1'b0, p3[1]};

  logic [4:0] pos_acc, pos_new;
  logic       found_acc, found_new;
  assign pos_new   = pos_acc + p16;
  assign found_new = found_acc | (|eq_q);
  assign v2_fire   = v1 & (boundary_q | last_q);          // this line settles the result

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pos_acc <= 5'd0; found_acc <= 1'b0;
    end else if (state == S_IDLE) begin
      pos_acc <= 5'd0; found_acc <= 1'b0;                 // fresh accumulators for the next instruction
    end else if (v1) begin
      pos_acc   <= pos_new;
      found_acc <= found_new;
    end
  end

  // =========================================================== 4. rd register (writeback)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_valid <= 1'b0; rd_data <= 64'd0;
    end else begin
      rd_valid <= v2_fire;
      if (v2_fire) rd_data <= {55'd0, found_new, 3'd0, pos_new};
    end
  end

endmodule
