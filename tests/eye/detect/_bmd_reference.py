"""The authors' BMD, built and run for tests only (design spec
`2026-09-27-bmd-design.md` section 2).

`github.com/basvanopheusden/BMD` has no licence file. The requester stated on
2026-09-27 that its authors permit its use for testing. So:
- the checkout sits outside this repository;
- `WLPP_BMD_REFERENCE` names it;
- nothing from it is committed or installed.

The build copies `bmd.cpp`/`bmd.h` into a build directory inside the checkout
and patches the copies, by pattern, never by carrying their text:
- the seed comes from `BMD_SEED`, not the clock;
- lambda0, lambda1 and the two sigma caps are compile-time constants;
- `x` and `zf` get one element of 0 on each side, so the two reads past
  their ends (spec 1.10 item 1) are defined, and return the 0 they already
  return.

One more patch makes the settings' `long double` a `double`: the precision
the authors' stored example was produced with, on every platform (spec
1.10 item 5). It builds with clang against libc++, the random-number library
that example was produced with (spec 2).
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from wl_preproc.eye.detect.bmd_table import LogATable


def checkout() -> Path:
    root = os.environ.get("WLPP_BMD_REFERENCE")
    if not root:
        pytest.skip("WLPP_BMD_REFERENCE is not set (see design spec 2026-09-27-bmd-design.md section 2)")
    return Path(root)


@functools.lru_cache(maxsize=1)
def authors_table() -> LogATable:
    """The authors' `integral_table.txt`, in `bmd_table.LogATable` form."""
    with (checkout() / "integral_table.txt").open() as fh:
        n_alpha, n_d = (int(v) for v in fh.readline().split())
        values = np.array([[float(v) for v in fh.readline().split()[:n_d]] for _ in range(n_alpha)])
        lower = np.array([float(v) for v in fh.readline().split()[:n_d]])
        upper = np.array([float(v) for v in fh.readline().split()[:n_d]])
    return LogATable(values=np.ascontiguousarray(values.ravel()), alpha_lower=lower,
                     alpha_upper=upper, n_alpha=n_alpha, n_d=n_d)


def read_trace(path: Path) -> np.ndarray:
    """A trace in the reference's format: the length, then one `x y` per line."""
    with path.open() as fh:
        length = int(fh.readline())
        values = np.array([float(v) for line in fh for v in line.split()])
    return values.reshape(-1, 2)[:length]


def write_trace(path: Path, x: np.ndarray) -> None:
    """Full precision, so the reference parses back exactly the doubles the
    port was handed."""
    lines = [f"{len(x)}"] + [f"{a:.17g}\t{b:.17g}\t" for a, b in x]
    path.write_text("\n".join(lines) + "\n")


_PATCHES_H = [
    (r"lambda0=0\.004,\s*lambda1=0\.1", "lambda0=BMD_LAMBDA0, lambda1=BMD_LAMBDA1"),
    (r"\blong double\b", "double"),
]
_PATCHES_CPP = [
    (r"unsigned\(time\(0\)\)", '(unsigned) strtoul(getenv("BMD_SEED"), 0, 10)'),
    (r"(sigma=pow\(10\.0, -4\.0\+4\.0/100\.0\*sgi\);\s*)(theta_new\.set_d_sigma_up)",
     r"\1if (sgi > 0 && sigma > BMD_SIGMA1_MAX) continue;\n        \2"),
    (r"(sigma=pow\(10\.0, -4\.0\+4\.0/100\.0\*sgi\);\s*)(theta_new\.set_sigma_down)",
     r"\1if (sgi > 0 && sigma > BMD_SIGMA0_MAX) continue;\n        \2"),
    (r"\b(x|zf)\[(0|1)\]=new double\[T\];", r"\1[\2]=new double[T+2]()+1;"),
    (r"delete (x|zf)\[(0|1)\];", ""),
]


def _patched(text: str, patches: list[tuple[str, str]]) -> str:
    for pattern, replacement in patches:
        text, count = re.subn(pattern, replacement, text)
        assert count, f"the reference no longer matches {pattern!r}"
    return text


def _macro(value: float) -> str:
    return "INFINITY" if value == float("inf") else repr(float(value))


def binary(*, lambda0: float, lambda1: float, sigma0_cap: float = float("inf"),
           sigma1_cap: float = float("inf")) -> Path:
    """The patched reference built with these constants. Cached by
    everything that goes into the build: the sources after patching, and
    the compiler's arguments."""
    root = checkout()
    defines = {"BMD_LAMBDA0": _macro(lambda0), "BMD_LAMBDA1": _macro(lambda1),
               "BMD_SIGMA0_MAX": _macro(sigma0_cap), "BMD_SIGMA1_MAX": _macro(sigma1_cap)}
    header = _patched((root / "bmd.h").read_text(), _PATCHES_H)
    cpp = "#include <cstdlib>\n#include <cmath>\n" + _patched((root / "bmd.cpp").read_text(), _PATCHES_CPP)
    flags = [f"-D{k}={v}" for k, v in defines.items()]
    include = os.environ.get("WLPP_BMD_BOOST_INCLUDE")
    if include:
        flags.append(f"-I{include}")
    key = hashlib.sha1(repr((header, cpp, flags)).encode()).hexdigest()[:12]
    build = root / "_wlpp_build" / key
    exe = build / "bmd"
    if exe.exists():
        return exe
    build.mkdir(parents=True, exist_ok=True)
    (build / "bmd.h").write_text(header)
    (build / "bmd.cpp").write_text(cpp)
    command = ["clang++", *flags, "-std=c++14", "-stdlib=libc++", "-O3", "-o", str(exe), str(build / "bmd.cpp")]
    subprocess.run(command, check=True, capture_output=True)
    return exe


def run(exe: Path, x: np.ndarray, seed: int, workdir: Path) -> tuple[list[str], list[str]]:
    """Run the reference on `x`; its `params` and `changepoints` files' lines."""
    trace = workdir / "x.txt"
    write_trace(trace, x)
    params, changepoints = workdir / "params.txt", workdir / "changepoints.txt"
    for f in (params, changepoints):
        f.unlink(missing_ok=True)
    subprocess.run([str(exe), str(trace), str(checkout() / "integral_table.txt"), str(params),
                    str(changepoints)], check=True, capture_output=True,
                   env={**os.environ, "BMD_SEED": str(seed)})
    return params.read_text().splitlines(), changepoints.read_text().splitlines()


def formatted(record, stretch: int = 0) -> tuple[list[str], list[str]]:
    """A `bmd.BlockRecord` written the way the reference writes its files:
    `%g` to six significant digits, and each sample's `N`, `n`, `t01`, `t10`
    and `logpost` lines."""
    params = ["\t".join(f"{v:g}" for v in row) for row in record.settings]
    changepoints = []
    for iteration in record.samples:
        for n_ones, n, t01, t10, logpost in iteration[stretch]:
            changepoints += [f"N\t{n_ones}", f"n\t{n}", "t01\t" + "".join(f"{v}\t" for v in t01),
                             "t10\t" + "".join(f"{v}\t" for v in t10), f"logpost\t{logpost:g}"]
    return params, changepoints
