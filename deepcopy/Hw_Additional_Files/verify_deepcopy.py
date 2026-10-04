"""
Semantic equivalence tests for the deepcopy optimizations.

Everything here compares the optimized path against the stock
copy.deepcopy().  If any assertion fails the optimization is not a valid
deepcopy and the benchmark numbers are meaningless.

Run:  python3-dbg verify_deepcopy.py
"""
import copy
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fastdeepcopy"))
from fastdeepcopy import deepcopy as fast

PASS, FAIL = 0, 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  pass  %s" % label)
    else:
        FAIL += 1
        print("  FAIL  %s" % label)


# --------------------------------------------------------------------------
# 1. the C fast path must agree with copy.deepcopy on structure and identity
# --------------------------------------------------------------------------
print("[1] fastdeepcopy vs copy.deepcopy - structural equality")

shared = [1, 2, 3]
cases = {
    "flat list":        [1, 2, 3],
    "nested list":      [[1, 2], [3, [4, 5]]],
    "dict":             {"a": 1, "b": [1, 2], "c": {"d": (1, 2)}},
    "tuple of ints":    (1, 2, 3),
    "tuple of lists":   ([1], [2]),
    "empty containers": [[], {}, ()],
    "shared refs":      {"x": shared, "y": shared},
    "mixed atomics":    [1, "s", b"b", 1.5, True, False, None, 2j],
    "deep nest":        [[[[[1]]]]],
    "dict int keys":    {1: "a", (2, 3): "b"},
    "benchmark data":   {"a": ([1] * 100,) * 3, "b": [[1] * 100] * 100},
}
for name, obj in cases.items():
    check("%-18s equal" % name, fast(obj) == copy.deepcopy(obj))

# --------------------------------------------------------------------------
# 2. aliasing must be preserved, not duplicated
# --------------------------------------------------------------------------
print("[2] memo / aliasing")

s = [1, 2]
r = fast({"x": s, "y": s})
check("shared list stays shared", r["x"] is r["y"])
check("shared list is a copy", r["x"] is not s)

t = ([1],) * 3
rt = fast(t)
check("shared list inside tuple stays shared", rt[0] is rt[1] is rt[2])

# tuple of immutables must be returned as-is (copy._deepcopy_tuple behaviour)
imm = (1, 2, "three")
check("immutable tuple reused", fast(imm) is imm)
check("   ... same as stdlib", (copy.deepcopy(imm) is imm))

# --------------------------------------------------------------------------
# 3. recursive structures must not hang or crash
# --------------------------------------------------------------------------
print("[3] recursive structures")

rec = [1, 2]
rec.append(rec)
c = fast(rec)
check("self-referential list", c[2] is c and c is not rec)

rd = {"k": 1}
rd["self"] = rd
cd = fast(rd)
check("self-referential dict", cd["self"] is cd and cd is not rd)

# --------------------------------------------------------------------------
# 4. deep copies must be independent of the original
# --------------------------------------------------------------------------
print("[4] independence")

orig = {"lst": [[1, 2]], "d": {"k": [3]}}
cp = fast(orig)
cp["lst"][0].append(99)
cp["d"]["k"].append(99)
check("mutating copy leaves original alone",
      orig == {"lst": [[1, 2]], "d": {"k": [3]}})

# --------------------------------------------------------------------------
# 5. unknown types fall through to copy.deepcopy unchanged
# --------------------------------------------------------------------------
print("[5] fallback for non-builtin types")


class Plain:
    def __init__(self):
        self.v = [1, 2]

    def __eq__(self, o):
        return isinstance(o, Plain) and self.v == o.v


p = Plain()
cp = fast(p)
check("plain object copied", cp == p and cp is not p and cp.v is not p.v)
check("set copied", fast({1, 2, 3}) == {1, 2, 3})
check("frozenset copied", fast(frozenset([1, 2])) == frozenset([1, 2]))
check("list subclass keeps type", type(fast(type("L", (list,), {})([1]))).__name__ == "L")
check("dict subclass keeps type", type(fast(type("D", (dict,), {})(a=1))).__name__ == "D")


class Slotted:
    __slots__ = ("a",)

    def __init__(self):
        self.a = [1]


sl = fast(Slotted())
check("__slots__ object copied", sl.a == [1])

# --------------------------------------------------------------------------
# 6. the __deepcopy__ hooks used by the benchmark
# --------------------------------------------------------------------------
print("[6] __deepcopy__ hooks - must match generic deepcopy semantics")


@dataclass
class Ref:                      # what upstream does: no hook at all
    string: str
    lst: list
    boolean: bool


@dataclass
class Opt:                      # the optimized hook
    string: str
    lst: list
    boolean: bool

    def __deepcopy__(self, memo):
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        new.string = self.string
        new.boolean = self.boolean
        new.lst = copy.deepcopy(self.lst, memo)
        return new


def hook_suite(cls, tag):
    x = cls("s", [1, 2], True)
    d = copy.deepcopy({"p": x, "q": x})
    check("%s aliasing preserved" % tag, d["p"] is d["q"])

    y = cls("s", [[1, 2], {"k": "v"}], True)
    cy = copy.deepcopy(y)
    check("%s nested list deep" % tag, cy.lst[0] is not y.lst[0])
    check("%s nested dict deep" % tag, cy.lst[1] is not y.lst[1])
    cy.lst[0].append(99)
    check("%s copy is independent" % tag, y.lst[0] == [1, 2])

    Sub = type("Sub", (cls,), {})
    check("%s subclass preserved" % tag,
          type(copy.deepcopy(Sub("s", [1], True))) is Sub)

    z = cls("s", [], True)
    z.lst.append(z)
    try:
        rz = copy.deepcopy(z)
        check("%s self-reference works" % tag, rz.lst[0] is rz)
    except RecursionError:
        check("%s self-reference works" % tag, False)


hook_suite(Ref, "upstream")
hook_suite(Opt, "optimized")

# The two suites above exercise a local copy of the hook. Run the same suite
# against the REAL class imported from optimized_deepcopy.py, so that editing
# the hook in the benchmark itself cannot silently pass this file.
try:
    from optimized_deepcopy import A as RealA
except Exception as exc:                                  # pragma: no cover
    print("  skip  cannot import optimized_deepcopy (%s)" % exc)
else:
    hook_suite(RealA, "REAL A")

# the reduce-protocol class from benchmark_reduce
print("[7] benchmark_reduce class - hook vs __reduce__ path")


def make_C(with_hook):
    ns = {
        "__init__": lambda self: (setattr(self, "a", 1), setattr(self, "b", [2]))[0],
        "__reduce__": lambda self: (make_C(with_hook), (), self.__dict__),
        "__setstate__": lambda self, st: self.__dict__.update(st),
    }
    if with_hook:
        def __deepcopy__(self, memo):
            cls = self.__class__
            new = cls.__new__(cls)
            memo[id(self)] = new
            d = new.__dict__
            for k, v in self.__dict__.items():
                d[k] = copy.deepcopy(v, memo)
            return new
        ns["__deepcopy__"] = __deepcopy__
    return type("C", (object,), ns)


ref_obj = make_C(False)()
opt_obj = make_C(True)()
ref_cp = copy.deepcopy(ref_obj)
opt_cp = copy.deepcopy(opt_obj)
check("same __dict__ produced", ref_cp.__dict__ == opt_cp.__dict__ == {"a": 1, "b": [2]})
check("mutable state deep-copied", opt_cp.b is not opt_obj.b)
opt_cp.b.append(9)
check("original untouched", opt_obj.b == [2])

# --------------------------------------------------------------------------
# 8. reference-count leak check (debug builds only)
# --------------------------------------------------------------------------
print("[8] reference leaks (python3-dbg only)")

if hasattr(sys, "gettotalrefcount"):
    payload = {"a": ([1] * 100,) * 3, "b": [[1] * 100] * 100,
               "c": {"k": [1, (2, 3), "s"]}}
    for _ in range(50):                       # settle caches
        fast(payload)
    before = sys.gettotalrefcount()
    for _ in range(500):
        fast(payload)
    after = sys.gettotalrefcount()
    check("no refcount growth over 500 copies (delta=%d)" % (after - before),
          abs(after - before) < 100)
else:
    print("  skip  not a debug build - run with python3-dbg to check leaks")


print()
print("=" * 52)
print(" %d passed, %d failed" % (PASS, FAIL))
print("=" * 52)
sys.exit(1 if FAIL else 0)
