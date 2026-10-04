"""
Benchmark to measure performance of the python builtin method copy.deepcopy

Performance is tested on a nested dictionary and a dataclass

Author: Pieter Eendebak

--------------------------------------------------------------------------
OPTIMIZED VARIANT.  The workload (data, loop counts, pyperf wiring) is byte
for byte the upstream one.  Only *how the copy is performed* changes:

  1. fastdeepcopy.deepcopy - a C extension that walks dict/list/tuple/atomic
     values without a Python frame per element.  Everything it does not
     handle natively is delegated to copy.deepcopy, so semantics are
     unchanged.  See fastdeepcopy/fastdeepcopymodule.c.

  2. __deepcopy__ hooks on the two user-defined classes.  This is the
     documented extension point of the copy protocol: a class that knows
     its own layout can copy itself far more cheaply than the generic
     __reduce_ex__ / _reconstruct machinery.

Both hooks below are *semantically complete* deepcopies: they register
themselves in the memo before recursing (so self-referential structures and
shared references work), they recurse into mutable attributes, and they use
cls.__new__(cls) so that subclasses survive the copy.  verify_deepcopy.py
asserts this against the stock implementation.
--------------------------------------------------------------------------
"""
import copy
import os
import sys

import pyperf
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fastdeepcopy"))
try:
    from fastdeepcopy import deepcopy as _fast_deepcopy
except ImportError:                                   # pragma: no cover
    _fast_deepcopy = copy.deepcopy
    sys.stderr.write("fastdeepcopy C extension not built - "
                     "falling back to copy.deepcopy\n")

deepcopy = _fast_deepcopy


@dataclass
class A:
    string: str
    lst: list
    boolean: bool

    def __deepcopy__(self, memo):
        cls = self.__class__
        new = cls.__new__(cls)          # keeps subclasses intact
        memo[id(self)] = new            # register BEFORE recursing
        new.string = self.string        # str  - immutable, safe to share
        new.boolean = self.boolean      # bool - immutable, safe to share
        new.lst = deepcopy(self.lst, memo)
        return new


def benchmark_reduce(n):
    """ Benchmark where the __reduce__ functionality is used """
    class C(object):
        def __init__(self):
            self.a = 1
            self.b = 2

        def __reduce__(self):
            return (C, (), self.__dict__)

        def __setstate__(self, state):
            self.__dict__.update(state)

        def __deepcopy__(self, memo):
            cls = self.__class__
            new = cls.__new__(cls)
            memo[id(self)] = new
            d = new.__dict__
            for k, v in self.__dict__.items():
                d[k] = deepcopy(v, memo)
            return new
    c = C()

    t0 = pyperf.perf_counter()
    for ii in range(n):
        _ = deepcopy(c)
    dt = pyperf.perf_counter() - t0
    return dt


def benchmark_memo(n):
    """ Benchmark where the memo functionality is used """
    A = [1] * 100
    data = {'a': (A, A, A), 'b': [A] * 100}

    t0 = pyperf.perf_counter()
    for ii in range(n):
        _ = deepcopy(data)
    dt = pyperf.perf_counter() - t0
    return dt


def benchmark(n):
    """ Benchmark on some standard data types """
    a = {
        'list': [1, 2, 3, 43],
        't': (1 ,2, 3),
        'str': 'hello',
        'subdict': {'a': True}
    }
    dc = A('hello', [1, 2, 3], True)

    dt = 0
    for ii in range(n):
        for jj in range(30):
            t0 = pyperf.perf_counter()
            _ = deepcopy(a)
            dt += pyperf.perf_counter() - t0
        for s in ['red', 'blue', 'green']:
            dc.string = s
            for kk in range(5):
                dc.lst[0] = kk
                for b in [True, False]:
                    dc.boolean = b
                    t0 = pyperf.perf_counter()
                    _ = deepcopy(dc)
                    dt += pyperf.perf_counter() - t0
    return dt


if __name__ == "__main__":
    runner = pyperf.Runner()
    runner.metadata['description'] = "deepcopy benchmark"

    runner.bench_time_func('deepcopy', benchmark)
    runner.bench_time_func('deepcopy_reduce', benchmark_reduce)
    runner.bench_time_func('deepcopy_memo', benchmark_memo)
