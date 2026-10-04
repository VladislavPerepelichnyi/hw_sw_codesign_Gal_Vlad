"""
Prove that the optimized benchmark measures the SAME workload as the baseline.

This is the integrity check that matters most for this project: a "speedup"
obtained by quietly shrinking the work is not a speedup. The script compares
the abstract syntax trees of the three benchmark functions in
baseline_<bench>.py and optimized_<bench>.py after normalising away exactly
the things the optimization is allowed to change:

  * calls to copy.deepcopy(...) / deepcopy(...) / _dc(...) are all rewritten
    to a single canonical __DC__(...) node, so swapping the copy routine is
    invisible;
  * an added __deepcopy__ method is removed, since adding a copy hook is the
    optimization itself.

Everything else - the data literals, the loop bounds, the pyperf timer
placement, the registered benchmark names - must match exactly.

Usage:  python3 check_workload.py [baseline.py] [optimized.py]
"""
import ast
import sys

DC_NAMES = {"deepcopy", "_dc", "_fast_deepcopy"}


class Normalise(ast.NodeTransformer):
    """Erase the differences the optimization is permitted to introduce."""

    def visit_FunctionDef(self, node):
        if node.name == "__deepcopy__":
            return None                      # the hook IS the optimization
        self.generic_visit(node)
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        f = node.func
        is_dc = (isinstance(f, ast.Name) and f.id in DC_NAMES) or (
            isinstance(f, ast.Attribute)
            and f.attr == "deepcopy"
            and isinstance(f.value, ast.Name)
            and f.value.id in ("copy", "fastdeepcopy")
        )
        if is_dc:
            node.func = ast.Name(id="__DC__", ctx=ast.Load())
            # drop an explicit memo argument: deepcopy(x, memo) == deepcopy(x)
            node.args = node.args[:1]
        return node


def top_level(tree, name):
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name:
            return n
    return None


def dump(node):
    if node is None:
        return None
    node = Normalise().visit(ast.parse(ast.unparse(node)))
    return ast.dump(node, include_attributes=False)


def registered_benchmarks(tree):
    """Names passed to runner.bench_time_func('<name>', fn)."""
    out = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "bench_time_func"
                and n.args
                and isinstance(n.args[0], ast.Constant)):
            out.append((n.args[0].value, getattr(n.args[1], "id", "?")))
    return out


def main(base_path, opt_path):
    base = ast.parse(open(base_path).read())
    opt = ast.parse(open(opt_path).read())

    failures = 0

    def check(label, ok):
        nonlocal failures
        print("  %s  %s" % ("pass" if ok else "FAIL", label))
        if not ok:
            failures += 1

    for fn in ("benchmark", "benchmark_reduce", "benchmark_memo"):
        b, o = top_level(base, fn), top_level(opt, fn)
        if b is None or o is None:
            check("%s present in both files" % fn, False)
            continue
        check("%s() workload identical" % fn, dump(b) == dump(o))

    # the dataclass the benchmark copies must keep the same shape
    bA, oA = top_level(base, "A"), top_level(opt, "A")
    if bA and oA:
        fields = lambda c: [(s.target.id, ast.unparse(s.annotation))
                            for s in c.body if isinstance(s, ast.AnnAssign)]
        check("dataclass A fields identical", fields(bA) == fields(oA))
    else:
        check("dataclass A present in both files", False)

    rb, ro = registered_benchmarks(base), registered_benchmarks(opt)
    check("same benchmarks registered (%s)" % ", ".join(n for n, _ in rb),
          rb == ro and len(rb) == 3)

    return failures


if __name__ == "__main__":
    b = sys.argv[1] if len(sys.argv) > 1 else "baseline_deepcopy.py"
    o = sys.argv[2] if len(sys.argv) > 2 else "optimized_deepcopy.py"
    print("Comparing workload: %s  vs  %s" % (b, o))
    sys.exit(1 if main(b, o) else 0)
