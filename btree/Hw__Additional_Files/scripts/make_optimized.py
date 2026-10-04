#!/usr/bin/env python3
"""Generate benchmark/optimized/bm_btree/run_benchmark.py from benchmark/original/bm_btree
(the committed, verbatim pyperformance 1.14.0 benchmark) by applying the in-node search optimization:

  * import bisect_left
  * BNode gets a parallel `keys` list (plain ints) next to `items`
  * get_position  ->  get_position_opt = bisect_left(self.keys, key)
  * every mutation of `items` is mirrored on `keys` (one line each)

Nothing else in the benchmark changes.  The script is line-anchored and
asserts the expected text at every anchor, so it fails loudly on a
different pyperformance version instead of producing a wrong file."""
import os, shutil, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(REPO, "benchmark", "original", "bm_btree")
DST_DIR = os.path.join(REPO, "benchmark", "optimized", "bm_btree")

src = open(os.path.join(SRC_DIR, "run_benchmark.py")).read().split("\n")

def indent(line):
    return line[: len(line) - len(line.lstrip())]

# (1-based line, expected substring, list of lines to append after it, mirrored with same indent)
AFTER = [
    (40,  "self.items = []",                                   ["self.keys = []"]),
    (84,  "self.items.insert(position, item)",                 ["self.keys.insert(position, key)"]),
    (107, "bigger.items = child.items[middle + 1 :]",          ["bigger.keys = child.keys[middle + 1 :]"]),
    (108, "child.items = child.items[:middle]",                ["child.keys = child.keys[:middle]"]),
    (114, "self.items.insert(position, splitting_key)",        ["self.keys.insert(position, splitting_key[0])"]),
    (170, "del self.items[p]",                                 ["del self.keys[p]"]),
    (183, "self.items[p] = extreme",                           ["self.keys[p] = extreme[0]"]),
    (188, "self.items[p] = extreme",                           ["self.keys[p] = extreme[0]"]),
    (193, "node.items = node.items + [extreme] + upper_sibling.items",
                                                               ["node.keys = node.keys + [extreme[0]] + upper_sibling.keys"]),
    (196, "del self.items[p]",                                 ["del self.keys[p]"]),
    (202, "node.items.insert(0, self.items[p - 1])",           ["node.keys.insert(0, self.keys[p - 1])"]),
    (203, "self.items[p - 1] = lower_sibling.items[-1]",       ["self.keys[p - 1] = lower_sibling.keys[-1]"]),
    (204, "del lower_sibling.items[-1]",                       ["del lower_sibling.keys[-1]"]),
    (210, "node.items.append(self.items[p])",                  ["node.keys.append(self.keys[p])"]),
    (211, "self.items[p] = upper_sibling.items[0]",            ["self.keys[p] = upper_sibling.keys[0]"]),
    (212, "del upper_sibling.items[0]",                        ["del upper_sibling.keys[0]"]),
    (222, ")",                                                 ["node.keys = lower_sibling.keys + [self.keys[p - 1]] + node.keys"]),
    (225, "del self.items[p - 1]",                             ["del self.keys[p - 1]"]),
    (231, ")",                                                 ["node.keys = node.keys + [self.keys[p]] + upper_sibling.keys"]),
    (234, "del self.items[p]",                                 ["del self.keys[p]"]),
    (240, "self.items = self.nodes[0].items",                  ["self.keys = self.nodes[0].keys"]),
]
REPLACE = {
    31: ("      items: list", ["      items: list", "      keys: list  # item[0] of every item, kept in step with items"]),
    35: ("__slots__ = ['items', 'nodes']", ["    __slots__ = ['items', 'keys', 'nodes']"]),
    58: ("def get_position(self, key):", ["    def get_position_opt(self, key):",
                                         "        return bisect_left(self.keys, key)",
                                         "",
                                         "    get_position = get_position_opt  # call sites unchanged"]),
}
DELETE = {59, 60, 61, 62}          # body of the old linear-scan get_position
IMPORT_AFTER = (7, "import gc", ["from bisect import bisect_left"])  # keep import block sorted-ish

# checks
for ln, exp, _ in AFTER:
    assert exp in src[ln - 1], f"line {ln}: expected {exp!r}, got {src[ln-1]!r}"
for ln, (exp, _) in REPLACE.items():
    assert exp in src[ln - 1], f"line {ln}: expected {exp!r}, got {src[ln-1]!r}"
assert src[IMPORT_AFTER[0] - 1].strip() == IMPORT_AFTER[1], src[IMPORT_AFTER[0] - 1]
# the two ')' anchors close the multi-line concatenations in delete()
assert "node.items = (" in src[217] and "node.items = (" in src[228]

out = []
after = {ln: lines for ln, _, lines in AFTER}
for i, line in enumerate(src, 1):
    if i in DELETE:
        continue
    if i in REPLACE:
        out.extend(REPLACE[i][1])
    else:
        out.append(line)
    if i in after:
        ind = indent(line)
        out.extend(ind + l for l in after[i])
    if i == IMPORT_AFTER[0]:
        out.extend(IMPORT_AFTER[2])

os.makedirs(DST_DIR, exist_ok=True)
open(os.path.join(DST_DIR, "run_benchmark.py"), "w").write("\n".join(out))
# pyproject: same as the original's. (Outside the pyperformance tree a "dynamic" version
# cannot be resolved and `pyperformance run --manifest` aborts with "missing benchmark
# version", so benchmark/original/bm_btree/pyproject.toml carries an explicit version.)
pyproject = open(os.path.join(SRC_DIR, "pyproject.toml")).read()
pyproject = pyproject.replace('dynamic = ["version"]', 'version = "1.14.0"')   # in case a verbatim pyproject is dropped in
open(os.path.join(DST_DIR, "pyproject.toml"), "w").write(pyproject)
with open(os.path.join(DST_DIR, "..", "MANIFEST"), "w") as f:
    f.write("[benchmarks]\n\nname\tmetafile\nbtree\t<local>\n")
print("wrote", os.path.join(DST_DIR, "run_benchmark.py"))
