/*
 * fastdeepcopy - a C implementation of copy.deepcopy() for the common
 * built-in container types.
 *
 * Motivation
 * ----------
 * copy.deepcopy() is pure Python.  Every single element of every nested
 * container costs a Python-level function call into copy.deepcopy(), plus a
 * dict lookup in _deepcopy_dispatch, plus a memo lookup.  For the pyperformance
 * "deepcopy" benchmark family the data is almost entirely dict/list/tuple/int,
 * so that per-element interpreter overhead dominates the run time.
 *
 * This module walks dict/list/tuple/atomic values entirely in C, keeping the
 * exact semantics of copy.deepcopy():
 *
 *   - memo is a dict mapping id(original) -> copy, shared with copy.deepcopy
 *     so that the fallback path below stays consistent.
 *   - originals are kept alive through memo[id(memo)] exactly like
 *     copy._keep_alive(), so ids cannot be recycled mid-copy.
 *   - tuples reuse the original object when no element actually changed,
 *     matching copy._deepcopy_tuple().
 *   - a user-defined __deepcopy__ hook is honoured.
 *   - ANY type we do not handle natively is delegated to copy.deepcopy(x, memo),
 *     so behaviour is never silently different - only faster.
 *
 * Only exact types take the fast path (Py_IS_TYPE, not PyXxx_Check), because a
 * dict/list/tuple subclass may carry extra state that must go through the
 * generic __reduce_ex__ machinery.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

static PyObject *copy_deepcopy = NULL;   /* the stdlib copy.deepcopy */
static PyObject *str_deepcopy_hook = NULL; /* interned "__deepcopy__" */

static PyObject *fd_deepcopy(PyObject *x, PyObject *memo);

/* ---------- memo helpers ------------------------------------------------ */

/* memo[id(orig)] = copy */
static int
memo_put(PyObject *memo, PyObject *orig, PyObject *copy)
{
    PyObject *key = PyLong_FromVoidPtr((void *)orig);
    if (key == NULL) {
        return -1;
    }
    int rc = PyDict_SetItem(memo, key, copy);
    Py_DECREF(key);
    return rc;
}

/* borrowed reference, or NULL (with no exception set) if absent */
static PyObject *
memo_get(PyObject *memo, PyObject *orig)
{
    PyObject *key = PyLong_FromVoidPtr((void *)orig);
    if (key == NULL) {
        return NULL;
    }
    PyObject *val = PyDict_GetItemWithError(memo, key);
    Py_DECREF(key);
    return val;
}

/*
 * Mirror of copy._keep_alive(): stash a reference to the original inside the
 * memo so that it cannot be garbage collected (and its id reused for a
 * different object) while the copy is still in progress.
 */
static int
keep_alive(PyObject *x, PyObject *memo)
{
    PyObject *key = PyLong_FromVoidPtr((void *)memo);
    if (key == NULL) {
        return -1;
    }
    PyObject *lst = PyDict_GetItemWithError(memo, key);  /* borrowed */
    if (lst != NULL) {
        int rc = PyList_Append(lst, x);
        Py_DECREF(key);
        return rc;
    }
    if (PyErr_Occurred()) {
        Py_DECREF(key);
        return -1;
    }
    lst = PyList_New(0);
    if (lst == NULL) {
        Py_DECREF(key);
        return -1;
    }
    if (PyList_Append(lst, x) < 0) {
        Py_DECREF(lst);
        Py_DECREF(key);
        return -1;
    }
    int rc = PyDict_SetItem(memo, key, lst);
    Py_DECREF(lst);
    Py_DECREF(key);
    return rc;
}

/* ---------- type classification ----------------------------------------- */

/*
 * Immutable / shareable values: copy.deepcopy returns these unchanged.
 * Ordered roughly by how often they show up in the benchmark data.
 */
static inline int
is_atomic(PyObject *o)
{
    PyTypeObject *t = Py_TYPE(o);
    if (t == &PyLong_Type || t == &PyUnicode_Type || t == &PyBool_Type ||
        t == &PyFloat_Type || t == &PyBytes_Type || t == &PyComplex_Type) {
        return 1;
    }
    if (o == Py_None || o == Py_Ellipsis || o == Py_NotImplemented) {
        return 1;
    }
    if (t == &PyRange_Type || t == &PySlice_Type ||
        t == &PyFunction_Type || t == &PyCFunction_Type) {
        return 1;
    }
    /* classes are atomic: copy.deepcopy does `if issubclass(cls, type)` */
    if (PyType_Check(o)) {
        return 1;
    }
    return 0;
}

/* ---------- container copiers ------------------------------------------- */

static PyObject *
deepcopy_list(PyObject *x, PyObject *memo)
{
    Py_ssize_t n = PyList_GET_SIZE(x);
    PyObject *out = PyList_New(n);
    if (out == NULL) {
        return NULL;
    }
    /* Pre-fill with None so the list is always in a readable state even if a
     * user __deepcopy__ reaches it through the memo while we are still
     * filling it in. */
    for (Py_ssize_t i = 0; i < n; i++) {
        Py_INCREF(Py_None);
        PyList_SET_ITEM(out, i, Py_None);
    }
    if (memo_put(memo, x, out) < 0) {
        Py_DECREF(out);
        return NULL;
    }
    for (Py_ssize_t i = 0; i < n; i++) {
        if (i >= PyList_GET_SIZE(x)) {      /* original shrank under us */
            break;
        }
        PyObject *item = PyList_GET_ITEM(x, i);
        Py_INCREF(item);
        PyObject *c = fd_deepcopy(item, memo);
        Py_DECREF(item);
        if (c == NULL) {
            Py_DECREF(out);
            return NULL;
        }
        PyObject *old = PyList_GET_ITEM(out, i);
        PyList_SET_ITEM(out, i, c);
        Py_DECREF(old);
    }
    if (keep_alive(x, memo) < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

static PyObject *
deepcopy_dict(PyObject *x, PyObject *memo)
{
    PyObject *out = PyDict_New();
    if (out == NULL) {
        return NULL;
    }
    if (memo_put(memo, x, out) < 0) {
        Py_DECREF(out);
        return NULL;
    }
    /* Snapshot the items: recursing can run arbitrary Python code, which may
     * mutate x, and PyDict_Next() must not be used across that. */
    PyObject *items = PyDict_Items(x);
    if (items == NULL) {
        Py_DECREF(out);
        return NULL;
    }
    Py_ssize_t n = PyList_GET_SIZE(items);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *kv = PyList_GET_ITEM(items, i);          /* borrowed */
        PyObject *k = PyTuple_GET_ITEM(kv, 0);             /* borrowed */
        PyObject *v = PyTuple_GET_ITEM(kv, 1);             /* borrowed */
        Py_INCREF(k);
        Py_INCREF(v);
        PyObject *ck = fd_deepcopy(k, memo);
        if (ck == NULL) {
            Py_DECREF(k); Py_DECREF(v);
            Py_DECREF(items); Py_DECREF(out);
            return NULL;
        }
        PyObject *cv = fd_deepcopy(v, memo);
        Py_DECREF(k);
        Py_DECREF(v);
        if (cv == NULL) {
            Py_DECREF(ck);
            Py_DECREF(items); Py_DECREF(out);
            return NULL;
        }
        int rc = PyDict_SetItem(out, ck, cv);
        Py_DECREF(ck);
        Py_DECREF(cv);
        if (rc < 0) {
            Py_DECREF(items); Py_DECREF(out);
            return NULL;
        }
    }
    Py_DECREF(items);
    if (keep_alive(x, memo) < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

/* Mirrors copy._deepcopy_tuple(): reuse the original when nothing changed. */
static PyObject *
deepcopy_tuple(PyObject *x, PyObject *memo)
{
    Py_ssize_t n = PyTuple_GET_SIZE(x);
    if (n == 0) {
        Py_INCREF(x);
        return x;
    }
    PyObject *out = PyTuple_New(n);
    if (out == NULL) {
        return NULL;
    }
    int changed = 0;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *item = PyTuple_GET_ITEM(x, i);
        Py_INCREF(item);
        PyObject *c = fd_deepcopy(item, memo);
        Py_DECREF(item);
        if (c == NULL) {
            Py_DECREF(out);
            return NULL;
        }
        if (c != item) {
            changed = 1;
        }
        PyTuple_SET_ITEM(out, i, c);   /* steals reference */
    }
    /* A recursive structure may have produced the copy while we recursed. */
    PyObject *hit = memo_get(memo, x);
    if (hit != NULL) {
        Py_DECREF(out);
        Py_INCREF(hit);
        return hit;
    }
    if (PyErr_Occurred()) {
        Py_DECREF(out);
        return NULL;
    }
    if (!changed) {
        Py_DECREF(out);
        Py_INCREF(x);
        return x;
    }
    return out;
}

/* ---------- main dispatch ----------------------------------------------- */

static PyObject *
fd_deepcopy(PyObject *x, PyObject *memo)
{
    PyObject *hit = memo_get(memo, x);
    if (hit != NULL) {
        Py_INCREF(hit);
        return hit;
    }
    if (PyErr_Occurred()) {
        return NULL;
    }

    if (is_atomic(x)) {
        Py_INCREF(x);
        return x;
    }

    if (Py_EnterRecursiveCall(" in fastdeepcopy.deepcopy") != 0) {
        return NULL;
    }

    PyObject *y = NULL;
    PyTypeObject *t = Py_TYPE(x);

    if (t == &PyList_Type) {
        y = deepcopy_list(x, memo);
        Py_LeaveRecursiveCall();
        return y;
    }
    if (t == &PyDict_Type) {
        y = deepcopy_dict(x, memo);
        Py_LeaveRecursiveCall();
        return y;
    }
    if (t == &PyTuple_Type) {
        y = deepcopy_tuple(x, memo);
        Py_LeaveRecursiveCall();
        if (y != NULL && y != x) {
            if (memo_put(memo, x, y) < 0 || keep_alive(x, memo) < 0) {
                Py_DECREF(y);
                return NULL;
            }
        }
        return y;
    }

    /* Honour a user-supplied __deepcopy__ hook, as copy.deepcopy does. */
    PyObject *hook = PyObject_GetAttr(x, str_deepcopy_hook);
    if (hook != NULL) {
        y = PyObject_CallOneArg(hook, memo);
        Py_DECREF(hook);
        Py_LeaveRecursiveCall();
        if (y == NULL) {
            return NULL;
        }
        if (y != x) {
            if (memo_put(memo, x, y) < 0 || keep_alive(x, memo) < 0) {
                Py_DECREF(y);
                return NULL;
            }
        }
        return y;
    }
    if (!PyErr_ExceptionMatches(PyExc_AttributeError)) {
        Py_LeaveRecursiveCall();
        return NULL;
    }
    PyErr_Clear();

    /* Anything else: hand it to the stdlib, sharing our memo. */
    y = PyObject_CallFunctionObjArgs(copy_deepcopy, x, memo, NULL);
    Py_LeaveRecursiveCall();
    return y;
}

/* ---------- module surface ---------------------------------------------- */

PyDoc_STRVAR(deepcopy_doc,
"deepcopy(x, memo=None, /)\n--\n\n"
"Drop-in replacement for copy.deepcopy() with a C fast path for\n"
"dict, list, tuple and atomic immutables.  Any other type is delegated\n"
"to copy.deepcopy(), so the result is always identical.");

static PyObject *
fastdeepcopy_deepcopy(PyObject *self, PyObject *args)
{
    PyObject *x = NULL;
    PyObject *memo_arg = NULL;
    if (!PyArg_ParseTuple(args, "O|O:deepcopy", &x, &memo_arg)) {
        return NULL;
    }
    PyObject *memo;
    if (memo_arg == NULL || memo_arg == Py_None) {
        memo = PyDict_New();
        if (memo == NULL) {
            return NULL;
        }
    }
    else {
        if (!PyDict_Check(memo_arg)) {
            PyErr_SetString(PyExc_TypeError, "memo must be a dict or None");
            return NULL;
        }
        Py_INCREF(memo_arg);
        memo = memo_arg;
    }
    PyObject *result = fd_deepcopy(x, memo);
    Py_DECREF(memo);
    return result;
}

static PyMethodDef fastdeepcopy_methods[] = {
    {"deepcopy", fastdeepcopy_deepcopy, METH_VARARGS, deepcopy_doc},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef fastdeepcopy_module = {
    PyModuleDef_HEAD_INIT,
    "fastdeepcopy",
    "C fast path for copy.deepcopy() over built-in containers.",
    -1,
    fastdeepcopy_methods,
    NULL, NULL, NULL, NULL
};

PyMODINIT_FUNC
PyInit_fastdeepcopy(void)
{
    PyObject *copy_mod = PyImport_ImportModule("copy");
    if (copy_mod == NULL) {
        return NULL;
    }
    copy_deepcopy = PyObject_GetAttrString(copy_mod, "deepcopy");
    Py_DECREF(copy_mod);
    if (copy_deepcopy == NULL) {
        return NULL;
    }
    str_deepcopy_hook = PyUnicode_InternFromString("__deepcopy__");
    if (str_deepcopy_hook == NULL) {
        return NULL;
    }
    return PyModule_Create(&fastdeepcopy_module);
}
