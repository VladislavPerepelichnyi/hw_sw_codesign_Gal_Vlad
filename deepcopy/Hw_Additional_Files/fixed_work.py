"""
Fixed-work driver for hardware-counter comparison.

Why this file exists
--------------------
pyperf auto-calibrates how many inner loops to run so that each measurement
lasts a fixed amount of TIME.  That is exactly what you want for timing, but
it makes `perf stat` on the whole process useless for a before/after
comparison: code that is 5x faster simply runs ~5x more iterations, so total
instructions and cycles go UP even though every individual copy got cheaper.

This driver performs a FIXED number of deepcopy operations instead, so
instructions / cycles / cache-misses can be compared directly between the
baseline and the optimized implementation.

    perf stat -r 3 -- python3-dbg fixed_work.py --impl baseline
    perf stat -r 3 -- python3-dbg fixed_work.py --impl optimized
"""
import argparse
import copy
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fastdeepcopy"))

# Iteration counts chosen so the baseline spends a few seconds in each of the
# three workloads on the course VM's debug interpreter.
# Overridable so the test suite can run a tiny version of the same work.
ITERS_MAIN = int(os.environ.get("FW_ITERS_MAIN", 2000))      # 1 iter == 30 dict + 30 dataclass copies
ITERS_REDUCE = int(os.environ.get("FW_ITERS_REDUCE", 200000))
ITERS_MEMO = int(os.environ.get("FW_ITERS_MEMO", 20000))


@dataclass
class ABase:
    string: str
    lst: list
    boolean: bool


@dataclass
class AOpt:
    string: str
    lst: list
    boolean: bool

    def __deepcopy__(self, memo):
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        new.string = self.string
        new.boolean = self.boolean
        new.lst = _dc(self.lst, memo)
        return new


def make_C(with_hook):
    class C(object):
        def __init__(self):
            self.a = 1
            self.b = 2

        def __reduce__(self):
            return (C, (), self.__dict__)

        def __setstate__(self, state):
            self.__dict__.update(state)

    if with_hook:
        def __deepcopy__(self, memo):
            cls = self.__class__
            new = cls.__new__(cls)
            memo[id(self)] = new
            d = new.__dict__
            for k, v in self.__dict__.items():
                d[k] = _dc(v, memo)
            return new
        C.__deepcopy__ = __deepcopy__
    return C


def run(dc_cls):
    a = {
        'list': [1, 2, 3, 43],
        't': (1, 2, 3),
        'str': 'hello',
        'subdict': {'a': True}
    }
    dcobj = dc_cls('hello', [1, 2, 3], True)
    for _ in range(ITERS_MAIN):
        for _ in range(30):
            _dc(a)
        for s in ['red', 'blue', 'green']:
            dcobj.string = s
            for kk in range(5):
                dcobj.lst[0] = kk
                for b in [True, False]:
                    dcobj.boolean = b
                    _dc(dcobj)

    c = make_C(dc_cls is AOpt)()
    for _ in range(ITERS_REDUCE):
        _dc(c)

    A = [1] * 100
    data = {'a': (A, A, A), 'b': [A] * 100}
    for _ in range(ITERS_MEMO):
        _dc(data)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["baseline", "optimized"], required=True)
    args = ap.parse_args()

    if args.impl == "optimized":
        from fastdeepcopy import deepcopy as _dc
        cls = AOpt
    else:
        _dc = copy.deepcopy
        cls = ABase

    # _dc is referenced by the hooks above through the module globals
    globals()["_dc"] = _dc
    run(cls)
    sys.stderr.write("fixed_work done: impl=%s main=%d reduce=%d memo=%d\n"
                     % (args.impl, ITERS_MAIN, ITERS_REDUCE, ITERS_MEMO))
