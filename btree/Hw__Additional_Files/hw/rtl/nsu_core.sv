// =============================================================================
// nsu_core.sv — node search core of the fixed 31-lane design (t = 16: 31 keys)
//
//   position = number of keys k[i], i < count, with k[i] <  query   (bisect_left)
//   found    = some key    k[i], i < count, with k[i] == query
//
// Keys and query are 32-bit unsigned (the benchmark's keys are ints 0..199999).
//
//   query --------------------------+----------------+---- ... ----+
//                                   v                v             v
//   keys[0] -----------------> [ < ][ == ]   keys[1]-> [ < ][ == ]  ...  (31 lanes)
//   en[i] = (count > i) ------>  &    &                &    &
//                                lt[0] eq[0]           lt[1] eq[1]
//                                   |                       |
//                            stage-1 registers lt_q[30:0], eq_q[30:0]
//                                   |                       |
//                            adder tree (popcount)        31-input OR
//                                   |                       |
//                            stage-2 registers position[4:0], found
//
// 2 pipeline stages: result 2 cycles after in_valid, one search per cycle.
// =============================================================================
module nsu_core (
  input  logic              clk,
  input  logic              rst_n,

  input  logic              in_valid,
  input  logic [31:0]       in_query,
  input  logic [30:0][31:0] in_keys,       // in_keys[i] = key slot i
  input  logic [4:0]        in_count,      // valid key slots, 0..31

  output logic              out_valid,     // 2 cycles after in_valid
  output logic [4:0]        out_position,  // 0..31
  output logic              out_found
);

  // ---------------------------------------------------------------- stage 1
  logic [30:0] en, lt, eq;      // per-lane enable and comparator outputs
  logic [30:0] lt_q, eq_q;      // stage-1 registers
  logic        valid_q;

  genvar i;
  generate
    for (i = 0; i < 31; i++) begin : lane       // 31 identical lanes
      assign en[i] = (in_count > 5'(i));         // slot i holds a key
      assign lt[i] = en[i] & (in_keys[i] <  in_query);
      assign eq[i] = en[i] & (in_keys[i] == in_query);
    end
  endgenerate

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid_q <= 1'b0;
      lt_q    <= '0;
      eq_q    <= '0;
    end else begin
      valid_q <= in_valid;
      lt_q    <= lt;
      eq_q    <= eq;
    end
  end

  // ---------------------------------------------------------------- stage 2
  // popcount of lt_q as an adder tree: pairs at each level, odd element carried down
  logic [1:0] s1 [0:14];        // 15 x (1 bit + 1 bit)
  logic [2:0] s2 [0:6];         //  7 x (2 bit + 2 bit)
  logic [3:0] s3 [0:2];         //  3 x (3 bit + 3 bit)
  logic [4:0] s4;               //  1 x (4 bit + 4 bit)
  logic [4:0] position;

  genvar k;
  generate
    for (k = 0; k < 15; k++) begin : l1
      assign s1[k] = {1'b0, lt_q[2*k]} + {1'b0, lt_q[2*k+1]};
    end
    for (k = 0; k < 7; k++) begin : l2
      assign s2[k] = {1'b0, s1[2*k]} + {1'b0, s1[2*k+1]};
    end
    for (k = 0; k < 3; k++) begin : l3
      assign s3[k] = {1'b0, s2[2*k]} + {1'b0, s2[2*k+1]};
    end
  endgenerate
  assign s4       = {1'b0, s3[0]} + {1'b0, s3[1]};
  assign position = s4 + {1'b0, s3[2]} + {2'b00, s2[6]} + {3'b000, s1[14]} + {4'b0000, lt_q[30]};

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      out_valid    <= 1'b0;
      out_position <= '0;
      out_found    <= 1'b0;
    end else begin
      out_valid    <= valid_q;
      out_position <= position;
      out_found    <= |eq_q;
    end
  end

endmodule
