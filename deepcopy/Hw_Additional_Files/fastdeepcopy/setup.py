"""Build the fastdeepcopy C extension.

    python3-dbg fastdeepcopy/setup.py build_ext --inplace

The resulting .so lands next to this file; the benchmark adds this directory
to sys.path so `import fastdeepcopy` finds it without installing anything.
"""
from setuptools import setup, Extension

setup(
    name="fastdeepcopy",
    version="1.0",
    description="C fast path for copy.deepcopy() over built-in containers",
    ext_modules=[
        Extension(
            "fastdeepcopy",
            sources=["fastdeepcopymodule.c"],
            extra_compile_args=["-O2"],
        )
    ],
)
