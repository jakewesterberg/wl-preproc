"""numba's parallel kernels kept off OpenMP, for the whole process (U'n'Eye
design spec `2026-10-06-uneye-design.md`, amendments 6 and 8).

U'n'Eye's torch brings its own OpenMP runtime. With numba's kernels on a
second one, U'n'Eye's first convolution waits for ever where torch loads
second, and the process crashes where numba's runtime loads second. BMD's
kernels compute every sample on their own, so their results do not depend on
the thread pool they run on.
"""

from __future__ import annotations

import os

#: numba's own thread pool, which needs no OpenMP runtime.
LAYER = "workqueue"


def keep_off_openmp() -> None:
    """Every numba parallel kernel launched from now on runs on numba's own
    thread pool.

    Set in two places, because numba reads two. Its config decides the layer
    a kernel launches on; and when a `NUMBA_*` environment variable has
    changed, numba re-reads its environment at its next compile, overwriting
    that config. With the config alone, any such change put the layer back to
    what the environment asked for (the U'n'Eye-minors review's deferred
    minor). With the environment alone, a kernel numba loads from its cache
    without compiling, as BMD's are, never re-reads it: measured, BMD then ran
    on OpenMP on 3.13.

    It does not move kernels that have already launched: numba chooses its
    layer once per process. `uneye._network` refuses to start where that
    choice was OpenMP. The environment variable reaches the programs this
    process starts too; none of them uses numba today.

    **Writing the variable is itself a `NUMBA_*` change,** so numba's next
    compile rebuilds its whole config from the environment, dropping any
    value set directly on `numba.config` elsewhere. In a process that imports
    BMD or runs U'n'Eye, set numba's options through `NUMBA_*` variables."""
    import numba

    os.environ["NUMBA_THREADING_LAYER"] = LAYER
    numba.config.THREADING_LAYER = LAYER
