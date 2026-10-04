"""Bit-exact Python model of the Node Search Unit (hw/rtl/nsu_top.sv, hw/rtl/nsu_core.sv).

nsu_search(keys, count, query) -> (position, found)
  position = number of lanes i < count with keys[i] <  query
  found    = any lane  i < count with keys[i] == query
Keys and query are 32-bit unsigned values (the benchmark's keys are ints 0..199999).
For a sorted key list this equals (bisect_left(keys[:count], query), query in keys[:count]).
"""
LANES = 31
MASK = (1 << 32) - 1

def nsu_search(keys, count, query):
    assert 0 <= count <= LANES
    q = query & MASK
    pos = 0; found = False
    for i in range(count):
        k = keys[i] & MASK
        if k < q: pos += 1
        if k == q: found = True
    return pos, found

if __name__ == "__main__":
    import bisect, random
    rnd = random.Random(1)
    for _ in range(20000):
        n = rnd.randint(0, LANES)
        ks = sorted(rnd.sample(range(0, 5000), n))
        q = rnd.randint(0, 5200)
        p, f = nsu_search(ks, n, q)
        assert p == bisect.bisect_left(ks, q) and f == (q in ks)
    print("nsu_model: matches bisect_left on 20000 random sorted nodes")
