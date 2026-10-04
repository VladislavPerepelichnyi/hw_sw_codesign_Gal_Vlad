// =============================================================================
// memo_cache - hardware memo table for deepcopy()
// =============================================================================
//
// Hardware acceleration proposal for the deepcopy benchmark (see
// report_deepcopy.txt, section 5).
//
// A set-associative pointer -> pointer table on a Wishbone B4 (classic) slave.
// It takes over the memo of fastdeepcopy (fastdeepcopy/fastdeepcopymodule.c),
// which in software is a Python dict keyed by id(original):
//
//     memo_put(memo, orig, copy)   PyLong_FromVoidPtr + PyDict_SetItem   (insert)
//     memo_get(memo, orig)         PyLong_FromVoidPtr + PyDict_GetItem   (lookup)
//     new memo dict                once per top-level deepcopy call      (clear)
//
// Keys are object addresses used purely as opaque tags: the device never
// dereferences them. That is what makes it usable from Linux user space with
// no IOMMU, no address translation and no cache-coherency protocol - the only
// state is private to the device, and the CPU talks to it through uncached
// MMIO loads and stores (a driver maps the register page into the process).
//
// Organisation: SETS x WAYS entries (default 64 x 4 = 256). Each way has its
// own key and value memory with a registered read port, so the design maps
// onto FPGA block RAM. Valid bits are flip-flops so CLEAR is a single cycle.
// A lookup takes two cycles (read, compare); accesses that need its result
// stall the bus until it completes.
//
// The table is exact, never lossy: an insert into a full set is refused and
// reported (COMMIT returns 0), and software falls back to the memo dict.
// Silently evicting would drop a memo entry and break aliasing.
//
// Register map (32-bit words; byte offset / name / access):
//   0x00  ID         R   0x4D454D43 ("MEMC")
//   0x04  CFG        R   [7:0] log2(SETS)  [15:8] WAYS  [31:16] version
//   0x08  CTRL       W   bit0: clear table   bit1: reset statistics
//   0x0C  KEY        W   latch key, start lookup
//   0x10  VAL        W   latch value for the next COMMIT
//   0x14  RESULT     R   value stored for KEY, 0 if absent
//   0x18  COMMIT     R   insert/update (KEY, VAL): 1 = done, 0 = set full
//   0x1C  STATUS     R   bit0 last lookup hit  bit1 last commit ok  bit2 busy
//   0x20  LOOKUPS    R   RESULT reads since last stats reset
//   0x24  HITS       R   ... that hit
//   0x28  INSERTS    R   successful COMMITs
//   0x2C  FULL       R   refused COMMITs
//   0x30  OCCUPANCY  R   valid entries
//
// Software sequences:
//   lookup : write KEY; read RESULT                   (2 bus accesses)
//   insert : write KEY; write VAL; read COMMIT        (3 bus accesses)
//   clear  : write CTRL = 1                           (1 bus access)
// =============================================================================

`default_nettype none

module memo_cache #(
    parameter SET_BITS = 6,               // 64 sets
    parameter WAYS     = 4                // 4-way set associative
) (
    input  wire        clk,
    input  wire        rst,

    // Wishbone B4 classic slave, 32-bit data, word addressing (LiteX style)
    /* verilator lint_off UNUSEDSIGNAL */
    input  wire [29:0] wb_adr,     // only [3:0] used: the SoC decoder owns the rest
    input  wire [31:0] wb_dat_w,
    output reg  [31:0] wb_dat_r,
    input  wire [3:0]  wb_sel,     // every register is a full 32-bit word
    /* verilator lint_on UNUSEDSIGNAL */
    input  wire        wb_cyc,
    input  wire        wb_stb,
    input  wire        wb_we,
    output reg         wb_ack,
    output wire        wb_err
);

    localparam SETS    = 1 << SET_BITS;
    localparam WAY_BITS = (WAYS <= 1) ? 1 : $clog2(WAYS);
    localparam [31:0] MAGIC   = 32'h4D454D43;
    localparam [15:0] VERSION = 16'h0001;

    // register indices (word offsets)
    localparam [3:0] R_ID = 4'd0,  R_CFG = 4'd1,  R_CTRL = 4'd2,  R_KEY = 4'd3,
                     R_VAL = 4'd4, R_RESULT = 4'd5, R_COMMIT = 4'd6, R_STATUS = 4'd7,
                     R_LOOKUPS = 4'd8, R_HITS = 4'd9, R_INSERTS = 4'd10,
                     R_FULL = 4'd11, R_OCC = 4'd12;

    assign wb_err = 1'b0;

    // -------------------------------------------------------------------------
    // Set index: fold the key. Objects are 8-byte aligned, so skip bits [2:0].
    // -------------------------------------------------------------------------
    // The key is zero-extended so the last slice never reads past bit 31.
    function [SET_BITS-1:0] set_of(input [31:0] k);
        integer i;
        reg [SET_BITS-1:0] h;
        reg [31+SET_BITS:0] kp;
        begin
            kp = {{SET_BITS{1'b0}}, k};
            h  = {SET_BITS{1'b0}};
            for (i = 3; i < 32; i = i + SET_BITS)
                h = h ^ kp[i +: SET_BITS];
            set_of = h;
        end
    endfunction

    // -------------------------------------------------------------------------
    // Lookup pipeline state
    // -------------------------------------------------------------------------
    localparam [1:0] S_IDLE = 2'd0, S_READ = 2'd1, S_CMP = 2'd2;
    reg  [1:0]          state;
    wire                busy = (state != S_IDLE);

    reg  [31:0]         key_r, val_r;
    reg  [SET_BITS-1:0] set_r;

    reg                 hit_r;          // last lookup of key_r hit
    reg  [WAY_BITS-1:0] hitway_r;
    reg                 hasfree_r;      // key_r's set had a free way
    reg  [WAY_BITS-1:0] freeway_r;
    reg  [31:0]         result_r;
    reg                 commit_ok_r;

    reg  [WAYS*SETS-1:0] valid;         // valid[set*WAYS + way]
    reg  [WAYS-1:0]      rd_valid;      // valid bits of set_r, registered

    reg  [31:0] stat_lookups, stat_hits, stat_inserts, stat_full;
    reg  [SET_BITS+WAY_BITS:0] occupancy;

    // -------------------------------------------------------------------------
    // Bus decode
    // -------------------------------------------------------------------------
    wire       req     = wb_cyc & wb_stb & ~wb_ack;
    wire [3:0] reg_idx = wb_adr[3:0];

    // Anything issued while a lookup is in flight waits for it: that keeps
    // RESULT/COMMIT consistent and makes back-to-back KEY writes safe.
    wire       serve   = req & ~busy;

    wire [SET_BITS-1:0] set_in = set_of(wb_dat_w);   // set of an incoming KEY

    // COMMIT decision (combinational, used in the cycle the COMMIT is served)
    wire                commit_now = serve & ~wb_we & (reg_idx == R_COMMIT);
    wire                commit_ok  = hit_r | hasfree_r;
    wire [WAY_BITS-1:0] write_way  = hit_r ? hitway_r : freeway_r;
    wire                do_write   = commit_now & commit_ok;
    localparam VIDX_BITS = SET_BITS + WAY_BITS;
    wire [VIDX_BITS-1:0] ins_vidx  = {set_r, freeway_r};      // set*WAYS + way

    // -------------------------------------------------------------------------
    // Per-way key/value memories (registered read -> block RAM friendly)
    // -------------------------------------------------------------------------
    wire [31:0] rd_key [0:WAYS-1];
    wire [31:0] rd_val [0:WAYS-1];

    genvar w;
    generate
        for (w = 0; w < WAYS; w = w + 1) begin : g_way
            reg [31:0] kmem [0:SETS-1];
            reg [31:0] vmem [0:SETS-1];
            reg [31:0] rk, rv;
            always @(posedge clk) begin
                if (state == S_READ) begin
                    rk <= kmem[set_r];
                    rv <= vmem[set_r];
                end
                if (do_write && write_way == w) begin
                    kmem[set_r] <= key_r;
                    vmem[set_r] <= val_r;
                end
            end
            assign rd_key[w] = rk;
            assign rd_val[w] = rv;
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Compare stage (combinational over the registered read data)
    // -------------------------------------------------------------------------
    reg                 cmp_hit, cmp_hasfree;
    reg [WAY_BITS-1:0]  cmp_hitway, cmp_freeway;
    reg [31:0]          cmp_val;
    integer j;
    always @(*) begin
        cmp_hit     = 1'b0;
        cmp_hitway  = {WAY_BITS{1'b0}};
        cmp_val     = 32'd0;
        cmp_hasfree = 1'b0;
        cmp_freeway = {WAY_BITS{1'b0}};
        for (j = WAYS - 1; j >= 0; j = j - 1) begin   // lowest index wins
            if (rd_valid[j] && rd_key[j] == key_r) begin
                cmp_hit    = 1'b1;
                cmp_hitway = j[WAY_BITS-1:0];
                cmp_val    = rd_val[j];
            end
            if (!rd_valid[j]) begin
                cmp_hasfree = 1'b1;
                cmp_freeway = j[WAY_BITS-1:0];
            end
        end
    end

    // -------------------------------------------------------------------------
    // Control
    // -------------------------------------------------------------------------
    integer b;
    always @(posedge clk) begin
        if (rst) begin
            state        <= S_IDLE;
            wb_ack       <= 1'b0;
            wb_dat_r     <= 32'd0;
            key_r        <= 32'd0;
            val_r        <= 32'd0;
            set_r        <= {SET_BITS{1'b0}};
            hit_r        <= 1'b0;
            hitway_r     <= {WAY_BITS{1'b0}};
            hasfree_r    <= 1'b0;
            freeway_r    <= {WAY_BITS{1'b0}};
            result_r     <= 32'd0;
            commit_ok_r  <= 1'b0;
            valid        <= {WAYS*SETS{1'b0}};
            rd_valid     <= {WAYS{1'b0}};
            occupancy    <= 0;
            stat_lookups <= 32'd0;
            stat_hits    <= 32'd0;
            stat_inserts <= 32'd0;
            stat_full    <= 32'd0;
        end else begin
            wb_ack <= 1'b0;

            // ---- lookup pipeline ------------------------------------------
            case (state)
                S_READ: begin
                    for (b = 0; b < WAYS; b = b + 1)
                        rd_valid[b] <= valid[{set_r, b[WAY_BITS-1:0]}];
                    state <= S_CMP;
                end
                S_CMP: begin
                    hit_r     <= cmp_hit;
                    hitway_r  <= cmp_hitway;
                    hasfree_r <= cmp_hasfree;
                    freeway_r <= cmp_freeway;
                    result_r  <= cmp_hit ? cmp_val : 32'd0;
                    state     <= S_IDLE;
                end
                default: ;
            endcase

            // ---- bus ------------------------------------------------------
            if (serve) begin
                wb_ack <= 1'b1;
                if (wb_we) begin
                    case (reg_idx)
                        R_CTRL: begin
                            if (wb_dat_w[0]) begin
                                valid     <= {WAYS*SETS{1'b0}};
                                occupancy <= 0;
                                hit_r     <= 1'b0;
                                hasfree_r <= 1'b0;
                                result_r  <= 32'd0;
                            end
                            if (wb_dat_w[1]) begin
                                stat_lookups <= 32'd0;
                                stat_hits    <= 32'd0;
                                stat_inserts <= 32'd0;
                                stat_full    <= 32'd0;
                            end
                        end
                        R_KEY: begin
                            key_r <= wb_dat_w;
                            set_r <= set_in;
                            state <= S_READ;
                        end
                        R_VAL: val_r <= wb_dat_w;
                        default: ;    // writes to read-only registers ignored
                    endcase
                end else begin
                    case (reg_idx)
                        R_ID:      wb_dat_r <= MAGIC;
                        R_CFG:     wb_dat_r <= {VERSION, WAYS[7:0], SET_BITS[7:0]};
                        R_RESULT: begin
                            wb_dat_r     <= result_r;
                            stat_lookups <= stat_lookups + 1;
                            if (hit_r) stat_hits <= stat_hits + 1;
                        end
                        R_COMMIT: begin
                            wb_dat_r    <= {31'd0, commit_ok};
                            commit_ok_r <= commit_ok;
                            if (commit_ok) begin
                                stat_inserts <= stat_inserts + 1;
                                if (!hit_r) begin
                                    valid[ins_vidx] <= 1'b1;
                                    occupancy <= occupancy + 1;
                                end
                                // key_r is now present in write_way: a repeat
                                // COMMIT or RESULT sees the new value.
                                hit_r    <= 1'b1;
                                hitway_r <= write_way;
                                result_r <= val_r;
                            end else begin
                                stat_full <= stat_full + 1;
                            end
                        end
                        R_STATUS:  wb_dat_r <= {29'd0, busy, commit_ok_r, hit_r};
                        R_LOOKUPS: wb_dat_r <= stat_lookups;
                        R_HITS:    wb_dat_r <= stat_hits;
                        R_INSERTS: wb_dat_r <= stat_inserts;
                        R_FULL:    wb_dat_r <= stat_full;
                        R_OCC:     wb_dat_r <= {{(31-SET_BITS-WAY_BITS){1'b0}}, occupancy};
                        default:   wb_dat_r <= 32'd0;
                    endcase
                end
            end
        end
    end

endmodule

`default_nettype wire
