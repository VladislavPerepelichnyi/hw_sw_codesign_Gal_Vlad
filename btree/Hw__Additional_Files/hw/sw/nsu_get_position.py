"""
Software side of the Node Search Unit: how BNode.get_position would use the
`nsrch` instruction.

Integration sketch for hw/NSU_DESIGN.md §5. It shows

  * the node block layout the instruction reads: array.array('I') of 32 32-bit
    entries, entry 0 = key count, entries 1..31 = keys, 128 B (two cache lines);
  * the call boundary a C extension would implement with an inline-asm
    intrinsic, `nsrch(node_addr, query) -> (pos, found)`, here backed by the
    bit-exact Python model so the code path runs without the hardware;
  * the fallback to bisect_left when the instruction is absent or a key is
    outside 0 .. 2**32-1.

It is NOT wired into the benchmark: the course deliverable for the hardware
is the design + simulation; the projected gain over the bisect version is
bounded by the interpreter call overhead (design note §5.3).
"""
import array
import os
import sys
from bisect import bisect_left

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))
from nsu_model import nsu_search as _model_search, LANES

U32_MAX = (1 << 32) - 1
HAVE_NSRCH = False          # a real build sets this if the CPU reports the extension


def nsrch(block: array.array, query: int):
    """Stand-in for the C intrinsic: rd = nsrch(rs1 = &block[0], rs2 = query).
    Real implementation (C):  asm volatile("nsrch %0, %1, %2" : "=r"(rd) : "r"(addr), "r"(query));
                              return rd & 0x1F, bool(rd & 0x100)"""
    count = block[0]
    return _model_search(memoryview(block)[1:1 + count], count, query)


class BNodeHW:
    """Node whose keys live in a hardware-readable block. Only the parts relevant to
    the search are shown; items/nodes bookkeeping is as in benchmark/optimized/bm_btree."""

    __slots__ = ["items", "block", "nodes"]

    def __init__(self):
        self.items = []
        self.block = array.array("I", [0] * 32)      # entry 0 = key count, 1..31 = keys; 128 B (32-bit entries)
        self.nodes = None

    # --- the search: address + query in, position out --------------------------------
    def get_position_hw(self, key):
        if 0 <= key <= U32_MAX:
            pos, _found = nsrch(self.block, key)
            return pos
        return bisect_left(self.keys(), key)         # software fallback, same contract

    # --- mirroring on every mutation of items (one line each in the benchmark) ------
    def keys(self):
        return self.block[1:1 + self.block[0]]

    def insert(self, position, item):
        self.items.insert(position, item)
        n = self.block[0]
        for i in range(n, position, -1):             # shift keys right by one entry
            self.block[1 + i] = self.block[i]
        self.block[1 + position] = item[0]
        self.block[0] = n + 1


if __name__ == "__main__":
    import random
    rnd = random.Random(0)
    node = BNodeHW()
    for k in sorted(rnd.sample(range(0, 2000), 20)):
        node.insert(len(node.items), (k, None))
    assert node.block.itemsize == 4 and len(node.block) * 4 == 128, "node block = 2 cache lines"
    assert node.block[0] == 20 and list(node.keys()) == [it[0] for it in node.items]
    for q in list(node.keys()) + [rnd.randint(0, 2100) for _ in range(500)]:
        assert node.get_position_hw(q) == bisect_left(node.keys(), q)
    assert node.get_position_hw(1 << 40) == 20                      # falls back to software
    print("nsu_get_position: nsrch(node_addr, query) matches bisect_left on all queries; "
          "node block = 32 x 32-bit entries = 128 B, entry 0 = key count")
