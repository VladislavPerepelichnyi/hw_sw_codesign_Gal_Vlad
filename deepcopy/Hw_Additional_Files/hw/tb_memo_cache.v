// =============================================================================
// Testbench for memo_cache: directed tests + randomized check against a
// reference model.   iverilog -g2012 -o tb tb_memo_cache.v memo_cache.v && vvp tb
// =============================================================================
`timescale 1ns/1ps
`default_nettype none

module tb_memo_cache;

    localparam SET_BITS = 6, WAYS = 4, SETS = 1 << SET_BITS;
    localparam ID = 0, CFG = 1, CTRL = 2, KEY = 3, VAL = 4, RESULT = 5,
               COMMIT = 6, STATUS = 7, LOOKUPS = 8, HITS = 9, INSERTS = 10,
               FULL = 11, OCC = 12;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg  [29:0] adr;  reg [31:0] dat_w;  wire [31:0] dat_r;
    reg  cyc = 0, stb = 0, we = 0;  wire ack, err;

    memo_cache #(.SET_BITS(SET_BITS), .WAYS(WAYS)) dut (
        .clk(clk), .rst(rst),
        .wb_adr(adr), .wb_dat_w(dat_w), .wb_dat_r(dat_r), .wb_sel(4'hf),
        .wb_cyc(cyc), .wb_stb(stb), .wb_we(we), .wb_ack(ack), .wb_err(err));

    integer errors = 0, checks = 0, max_wait = 0;

    // ---- Wishbone master ---------------------------------------------------
    task wb_write(input [3:0] r, input [31:0] d);
        integer n;
        begin
            @(negedge clk); adr = {26'd0, r}; dat_w = d; we = 1; cyc = 1; stb = 1;
            n = 0; @(posedge clk); while (!ack) begin n = n + 1; @(posedge clk); end
            if (n > max_wait) max_wait = n;
            @(negedge clk); cyc = 0; stb = 0; we = 0;
        end
    endtask

    task wb_read(input [3:0] r, output [31:0] d);
        integer n;
        begin
            @(negedge clk); adr = {26'd0, r}; we = 0; cyc = 1; stb = 1;
            n = 0; @(posedge clk); while (!ack) begin n = n + 1; @(posedge clk); end
            if (n > max_wait) max_wait = n;
            d = dat_r;
            @(negedge clk); cyc = 0; stb = 0;
        end
    endtask

    // ---- driver-level helpers (what fastdeepcopy does) ----------------------
    task lookup(input [31:0] k, output [31:0] v);
        begin wb_write(KEY, k); wb_read(RESULT, v); end
    endtask
    task insert(input [31:0] k, input [31:0] v, output [31:0] ok);
        begin wb_write(KEY, k); wb_write(VAL, v); wb_read(COMMIT, ok); end
    endtask

    task chk(input [31:0] got, input [31:0] want, input [8*48-1:0] what);
        begin
            checks = checks + 1;
            if (got !== want) begin
                errors = errors + 1;
                $display("  FAIL %0s: got 0x%08x want 0x%08x", what, got, want);
            end
        end
    endtask

    // same fold as the RTL
    function [SET_BITS-1:0] set_of(input [31:0] k);
        integer i; reg [SET_BITS-1:0] h; reg [31+SET_BITS:0] kp;
        begin kp = {{SET_BITS{1'b0}}, k}; h = 0;
              for (i = 3; i < 32; i = i + SET_BITS) h = h ^ kp[i +: SET_BITS]; set_of = h; end
    endfunction

    // ---- reference model: simple arrays with linear search -----------------
    localparam MAXREF = 2048;
    reg [31:0] ref_k [0:MAXREF-1];
    reg [31:0] ref_v [0:MAXREF-1];
    integer    ref_n;
    integer    set_cnt [0:SETS-1];

    function integer ref_find(input [31:0] k);
        integer i;
        begin ref_find = -1; for (i = 0; i < ref_n; i = i + 1) if (ref_k[i] == k) ref_find = i; end
    endfunction

    reg [31:0] r, k, v, ok;
    integer i, t, idx, s, refused, want_ok;
    reg [31:0] sameset [0:5];

    initial begin
        repeat (3) @(posedge clk); rst = 0;

        $display("[1] identification");
        wb_read(ID, r);  chk(r, 32'h4D454D43, "ID");
        wb_read(CFG, r); chk(r, {16'h0001, 8'(WAYS), 8'(SET_BITS)}, "CFG");

        $display("[2] empty table misses");
        wb_write(CTRL, 1);
        lookup(32'h4000_1000, r); chk(r, 0, "miss on empty table");
        wb_read(OCC, r); chk(r, 0, "occupancy after clear");

        $display("[3] insert / lookup / update");
        insert(32'h4000_1000, 32'h4100_2000, ok); chk(ok, 1, "insert ok");
        lookup(32'h4000_1000, r); chk(r, 32'h4100_2000, "hit returns value");
        lookup(32'h4000_1008, r); chk(r, 0, "neighbour key misses");
        insert(32'h4000_1000, 32'h4100_3000, ok); chk(ok, 1, "update ok");
        lookup(32'h4000_1000, r); chk(r, 32'h4100_3000, "update visible");
        wb_read(OCC, r); chk(r, 1, "update does not add an entry");

        $display("[4] set conflict: %0d ways fill, the next is refused", WAYS);
        wb_write(CTRL, 3);               // clear table + stats
        // build WAYS+2 distinct keys that all land in one set
        s = 0;
        k = 32'h4000_0000;
        for (i = 0; i < WAYS + 2; i = i + 1) begin
            while (set_of(k) != set_of(32'h4000_0000)) k = k + 8;
            sameset[i] = k; k = k + 8;
        end
        for (i = 0; i < WAYS; i = i + 1) begin
            insert(sameset[i], 32'h5000_0000 + i, ok); chk(ok, 1, "fill way");
        end
        insert(sameset[WAYS], 32'h5000_00FF, ok); chk(ok, 0, "full set refuses insert");
        wb_read(FULL, r); chk(r, 1, "FULL counter");
        for (i = 0; i < WAYS; i = i + 1) begin
            lookup(sameset[i], r); chk(r, 32'h5000_0000 + i, "resident keys survive refusal");
        end
        lookup(sameset[WAYS], r); chk(r, 0, "refused key is absent (never lossy)");
        insert(sameset[1], 32'h5000_1111, ok); chk(ok, 1, "update in a full set still works");
        lookup(sameset[1], r); chk(r, 32'h5000_1111, "update in a full set visible");

        $display("[5] clear is total and single-shot");
        wb_write(CTRL, 1);
        for (i = 0; i < WAYS; i = i + 1) begin
            lookup(sameset[i], r); chk(r, 0, "miss after clear");
        end
        wb_read(OCC, r); chk(r, 0, "occupancy 0 after clear");

        $display("[6] statistics");
        wb_write(CTRL, 3);
        insert(32'h4000_2000, 32'h4200_0000, ok);
        lookup(32'h4000_2000, r); lookup(32'h4000_2008, r); lookup(32'h4000_2000, r);
        wb_read(LOOKUPS, r); chk(r, 3, "LOOKUPS");
        wb_read(HITS, r);    chk(r, 2, "HITS");
        wb_read(INSERTS, r); chk(r, 1, "INSERTS");

        $display("[7] randomized: 1500 ops vs reference model");
        wb_write(CTRL, 3);
        ref_n = 0; refused = 0;
        for (i = 0; i < SETS; i = i + 1) set_cnt[i] = 0;
        for (t = 0; t < 1500; t = t + 1) begin
            // 8-byte aligned keys from a pool small enough to produce repeats
            k = 32'h4000_0000 + (($urandom % 600) << 3);
            if (($urandom % 3) != 0) begin               // 2/3 inserts
                v = 32'h6000_0000 | ($urandom & 32'h00FF_FFF8) | 32'h8;
                insert(k, v, ok);
                idx = ref_find(k);
                if (idx >= 0) begin
                    want_ok = 1; ref_v[idx] = v;
                end else if (set_cnt[set_of(k)] < WAYS) begin
                    want_ok = 1; ref_k[ref_n] = k; ref_v[ref_n] = v; ref_n = ref_n + 1;
                    set_cnt[set_of(k)] = set_cnt[set_of(k)] + 1;
                end else begin
                    want_ok = 0; refused = refused + 1;
                end
                chk(ok, want_ok, "random insert result");
            end else begin                               // 1/3 lookups
                lookup(k, r);
                idx = ref_find(k);
                chk(r, (idx >= 0) ? ref_v[idx] : 32'd0, "random lookup result");
            end
        end
        wb_read(OCC, r); chk(r, ref_n, "occupancy matches model");
        // final sweep: every model entry is resident with the right value
        for (i = 0; i < ref_n; i = i + 1) begin
            lookup(ref_k[i], r); chk(r, ref_v[i], "final sweep");
        end
        $display("    model entries %0d, refused inserts %0d (full sets)", ref_n, refused);

        $display("");
        $display("==================================================");
        $display(" %0d checks, %0d errors   (max ack wait %0d cycles)", checks, errors, max_wait);
        $display("==================================================");
        $finish;
    end

    initial begin #20_000_000; $display("TIMEOUT"); $finish; end
endmodule

`default_nettype wire
