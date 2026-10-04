#!/usr/bin/env python3
"""Generate test vectors for the Node Search Unit from the REAL benchmark tree.

Loads the original bm_btree, builds the 200k-key tree exactly as the benchmark
does, then records (node keys, count, query) for every get_position call made
during a sample of lookups, inserts and deletes. Adds directed corner cases
(empty node, one key, full node, query below/above all keys, exact hits at
both ends, 32-bit extremes, count smaller than filled lanes). Expected results
come from hw/model/nsu_model.py and are cross-checked against bisect_left.
Keys are unsigned 32-bit, as in the unit (the benchmark's keys are 0..199999);
the file keeps one 64-bit hex word per value for $readmemh.

Output: hw/vectors/nsu_vectors.hex   ($readmemh, 34 x 64-bit values per vector)
          value 0      count
          value 1      query
          value 2..32  keys[0..30]  (unused lanes 0)
          value 33     expected: bit 8 = found, bits 4:0 = position
        hw/vectors/nsu_vectors.meta (number of vectors)
"""
import importlib.util, os, random, sys, bisect
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "model"))
from nsu_model import nsu_search, LANES, MASK

spec = importlib.util.spec_from_file_location("bm", os.path.join(ROOT, "benchmark", "original", "bm_btree", "run_benchmark.py"))
bm = importlib.util.module_from_spec(spec); spec.loader.exec_module(bm)

N_FROM_TREE = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
rnd = random.Random(7)
vectors = []   # (keys list, count, query)

# --- 1. vectors recorded from the real tree -----------------------------------
records = list(bm.make_records(bm.NUM_NODES))
random.seed(bm.RANDOM_SEED)
tree = bm.make_tree(bm.NUM_NODES, records)
recorded = []
orig_gp = bm.BNode.get_position
def recording_gp(self, key):
    recorded.append(([it[0] for it in self.items], key))
    return orig_gp(self, key)
bm.BNode.get_position = recording_gp
for _ in range(600):                       # lookups: hits and misses
    tree.get(rnd.randint(-10, bm.NUM_NODES + 10))
for k in range(bm.NUM_NODES, bm.NUM_NODES + 150):   # inserts (new keys)
    tree[k] = records[k % bm.NUM_NODES]
for k in rnd.sample(range(bm.NUM_NODES), 150):      # deletes (exercise all cases)
    del tree[k]
bm.BNode.get_position = orig_gp
rnd.shuffle(recorded)
for keys, q in recorded[:N_FROM_TREE]:
    vectors.append((keys, len(keys), q))

# --- 2. directed corner cases --------------------------------------------------
U32MAX = (1 << 32) - 1
full = sorted(rnd.sample(range(0, 10000), LANES))
cases = [
    ([], 0, 12345), ([], 0, 0),
    ([42], 1, 41), ([42], 1, 42), ([42], 1, 43),
    (full, LANES, full[0] - 1), (full, LANES, full[0]), (full, LANES, full[-1]),
    (full, LANES, full[-1] + 1), (full, LANES, full[15]), (full, LANES, full[15] + 1),
    ([0, 1, 2, U32MAX], 4, 0), ([0, 1, 2, U32MAX], 4, U32MAX), ([0, 1, 2, U32MAX], 4, 3),
    ([0, 1, 2, 1 << 31], 4, (1 << 31) - 1), ([0, 1, 2, 1 << 31], 4, 1 << 31),
    # count smaller than filled lanes: lanes >= count must be ignored
    (list(range(0, 62, 2)), 10, 100), (list(range(0, 62, 2)), 10, 5),
]
for keys, cnt, q in cases:
    vectors.append((keys, cnt, q))
# random nodes of every count 0..31
for cnt in range(0, LANES + 1):
    for _ in range(6):
        keys = sorted(rnd.sample(range(0, 1 << 32), cnt))
        q = rnd.choice(keys) if keys and rnd.random() < 0.4 else rnd.randint(0, (1 << 32) - 1)
        vectors.append((keys, cnt, q))

# --- 3. expected values + cross-check, write file ------------------------------
os.makedirs(os.path.join(HERE, "vectors"), exist_ok=True)
with open(os.path.join(HERE, "vectors", "nsu_vectors.hex"), "w") as f:
    for keys, cnt, q in vectors:
        pos, found = nsu_search(keys, cnt, q)
        assert pos == bisect.bisect_left(keys[:cnt], q) and found == (q in keys[:cnt])
        words = [cnt, q & MASK] + [k & MASK for k in keys[:cnt]] + [0] * (LANES - cnt)
        words.append((int(found) << 8) | pos)
        assert len(words) == 34
        f.write(" ".join(f"{w:016x}" for w in words) + "\n")
open(os.path.join(HERE, "vectors", "nsu_vectors.meta"), "w").write(f"{len(vectors)}\n")
n_hits = sum(1 for keys, c, q in vectors if q in keys[:c])
print(f"wrote {len(vectors)} vectors ({min(N_FROM_TREE, len(recorded))} from the real tree, {len(cases)} directed, "
      f"{6*(LANES+1)} random); {n_hits} exact hits; counts 0..{LANES}")
