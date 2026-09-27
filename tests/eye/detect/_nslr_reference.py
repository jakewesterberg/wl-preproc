"""The authors' own NSLR and NSLR-HMM, loaded by path -- never installed
(design spec `2026-09-27-nslr-design.md` section 7). Both are AGPL-3.0,
used test-only by the requester's decision of 2026-09-27.

`WLPP_NSLR_REFERENCE` names a directory holding the two checkouts at the
pinned commits: `nslr/` (67d03f8...) and `nslr-hmm/` (3598fee...). CI
fetches them (.github/workflows/ci.yml); locally they live outside the
repository.

**Two numpy adapters, on `nslr_hmm` alone** (spec section 2).
- numpy 2.4 rejects `float()` of a one-element array, which `nslr_hmm.py`
  298, 300 and 302 rely on. The adapter's `float` takes `.item()`, which is
  what older numpy's `float()` returned.
- numpy 2.5 removed `np.row_stack`, which `nslr_hmm.py` 80 calls with a
  single 1-D array. The adapter's `np` answers `row_stack` with
  `np.vstack`, identical for that argument, and passes everything else
  through.

Neither changes any arithmetic, and numpy itself is never patched.
"""

from __future__ import annotations

import builtins
import importlib.util
import os
import sys
import types
from functools import cache
from pathlib import Path

import numpy as np
import pytest


class _NumpyWithRowStack(types.ModuleType):
    def __getattr__(self, name):
        return np.vstack if name == "row_stack" else getattr(np, name)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@cache
def _loaded(root: str):
    slow_nslr = _load("_nslr_reference_slow_nslr", Path(root) / "nslr" / "nslr" / "slow_nslr.py")
    previous = sys.modules.get("nslr")
    # `nslr_hmm.py` does `import nslr` and calls `nslr.fit_gaze`.
    sys.modules["nslr"] = slow_nslr
    try:
        nslr_hmm = _load("_nslr_reference_nslr_hmm", Path(root) / "nslr-hmm" / "nslr_hmm.py")
    finally:
        if previous is None:
            sys.modules.pop("nslr", None)
        else:
            sys.modules["nslr"] = previous
    nslr_hmm.nslr = slow_nslr
    nslr_hmm.np = _NumpyWithRowStack("numpy_for_nslr_hmm")
    nslr_hmm.float = lambda value: builtins.float(np.asarray(value).item())
    return slow_nslr, nslr_hmm


def reference():
    """`(slow_nslr, nslr_hmm)`, or skip when `WLPP_NSLR_REFERENCE` is unset."""
    root = os.environ.get("WLPP_NSLR_REFERENCE")
    if not root:
        pytest.skip("WLPP_NSLR_REFERENCE is not set -- see tests/eye/detect/_nslr_reference.py")
    return _loaded(root)
