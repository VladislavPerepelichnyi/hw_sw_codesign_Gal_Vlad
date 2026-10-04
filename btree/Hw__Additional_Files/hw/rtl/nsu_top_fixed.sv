// =============================================================================
// nsu_top_fixed.sv — the fixed 31-lane design: the NSU execution unit as first built.
//   It waits for both lines of the node block and compares the 31 keys at once
//   (nsu_core). The unit proposed in the report is nsu_top.sv (streaming early-out),
//   which evolved from this one; this file is kept as the simpler reference design.
//
//     nsrch rd, rs1, rs2      rd = { found, position }   (bit 8, bits 4:0)
//
//   rs1 = address of the node block: 32 x 32-bit entries, 128 B aligned
//         entry 0      = key count (number of valid keys, 0..31)
//         entry 1..31  = keys k0..k30, sorted, unsigned
//         = exactly 2 cache lines of 64 B (16 entries each)
//   rs2 = query key in bits 31:0
//
// Structure (top to bottom):
//
//   1. Control            copies rs1 / rs2 on issue, walks IDLE -> FETCH -> COMPARE,
//                         issues the 2 line requests (rs1 + 64*j), one write enable
//                         per returned line, starts the core
//   2. Count + key regs   entry 0 -> count ; entries 1..31 -> key slots 0..30
//   3. nsu_core           31 lanes (cycle 1), adder tree + OR (cycle 2)
//   4. rd register        {found, position} + rd_valid for writeback
//
// One instruction in flight at a time (busy). Latency = 2 requests + L1 latency + 2.
// =============================================================================
module nsu_top_fixed (
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
  typedef enum logic [1:0] { S_IDLE, S_FETCH, S_COMPARE } state_t;
  state_t      state;
  logic [63:0] addr_q;                     // operand registers
  logic [31:0] query_q;
  logic [1:0]  req_idx, rsp_idx;           // lines requested / received, 0..2
  logic        accept, rsp_last, start_core;
  logic        core_done;                  // from the core (section 3)

  assign busy          = (state != S_IDLE);
  assign accept        = issue_valid & ~busy;
  assign mem_req_valid = (state == S_FETCH) & (req_idx != 2'd2);
  assign mem_req_addr  = addr_q + {57'd0, req_idx[0], 6'd0};     // rs1 + 64 B * line
  assign rsp_last      = mem_rsp_valid & (rsp_idx == 2'd1);

  // one write enable per line; line j carries entries 16j .. 16j+15
  logic [1:0] we_line;
  assign we_line[0] = (state == S_FETCH) & mem_rsp_valid & (rsp_idx == 2'd0);
  assign we_line[1] = (state == S_FETCH) & mem_rsp_valid & (rsp_idx == 2'd1);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state      <= S_IDLE;
      addr_q     <= 64'd0;
      query_q    <= 32'd0;
      req_idx    <= 2'd0;
      rsp_idx    <= 2'd0;
      start_core <= 1'b0;
    end else begin
      start_core <= 1'b0;
      case (state)
        S_IDLE: begin
          if (accept) begin                          // copy the operands, begin the 2-line fetch
            addr_q  <= rs1_addr;
            query_q <= rs2_query[31:0];
            req_idx <= 2'd0;
            rsp_idx <= 2'd0;
            state   <= S_FETCH;
          end
        end
        S_FETCH: begin
          if (mem_req_valid & mem_req_ready) req_idx <= req_idx + 2'd1;
          if (mem_rsp_valid)                 rsp_idx <= rsp_idx + 2'd1;
          if (rsp_last) begin                        // last line is written this edge
            start_core <= 1'b1;                      // core samples count + keys next cycle
            state      <= S_COMPARE;
          end
        end
        S_COMPARE: begin
          if (core_done) state <= S_IDLE;
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

  // =========================================================== 2. Count + key registers
  // entry 0 of line 0 is the count; key slot i is entry i+1: line (i+1)/16, slice (i+1)%16
  logic [4:0]        count_q;
  logic [30:0][31:0] keys_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n)            count_q <= 5'd0;
    else if (we_line[0])   count_q <= mem_rsp_data[4:0];
  end

  genvar i;
  generate
    for (i = 0; i < 31; i++) begin : keyreg
      always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)                      keys_q[i] <= 32'd0;
        else if (we_line[(i + 1) / 16])  keys_q[i] <= mem_rsp_data[((i + 1) % 16) * 32 +: 32];
      end
    end
  endgenerate

  // =========================================================== 3. nsu_core
  logic       core_found;
  logic [4:0] core_position;

  nsu_core u_core (
    .clk          (clk),
    .rst_n        (rst_n),
    .in_valid     (start_core),
    .in_query     (query_q),
    .in_keys      (keys_q),
    .in_count     (count_q),
    .out_valid    (core_done),
    .out_position (core_position),
    .out_found    (core_found)
  );

  // =========================================================== 4. rd register (writeback)
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rd_valid <= 1'b0;
      rd_data  <= 64'd0;
    end else begin
      rd_valid <= core_done;
      if (core_done) rd_data <= {55'd0, core_found, 3'd0, core_position};
    end
  end

endmodule
