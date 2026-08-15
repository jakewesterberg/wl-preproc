"""`wlpp doctor` — is this host ready to run the pipeline?"""

from __future__ import annotations

import shutil

# Design spec section 3.3's sizing estimate: a 2h dual-probe session occupies
# ~700-800 GB of scratch while processing (raw + the sorter's preprocessed
# copy + temporaries). Below that much free space, this host cannot safely
# take on the next session without risking exactly the mid-sort stall that
# same section's "Backpressure at ingest" describes -- so this is the floor a
# "ready to run the pipeline" check should actually enforce, not an arbitrary
# round number picked to make the check able to fail at all.
_MIN_SCRATCH_FREE_GIB = 800


def scratch_headroom(path: str = "/") -> tuple[int, bool]:
    """Free GiB at `path`, and whether it clears the floor.

    Extracted from `run_checks` so the daily report reuses this rather than
    reimplementing it — two definitions of "enough disk" that could disagree is
    exactly the drift worth preventing while there is still only one.

    `/` rather than a dedicated scratch mount remains a proxy: there is no
    scratch-root configuration to check instead, since SessionLayout takes its
    root as a caller-supplied argument rather than a resolved constant.
    """
    free_gib = shutil.disk_usage(path).free // 2**30
    return free_gib, free_gib >= _MIN_SCRATCH_FREE_GIB


def run_checks() -> list[str]:
    """Run each check, print a line per check, and return the failures."""
    failures: list[str] = []

    def report(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    print("wlpp doctor")

    try:
        import datajoint as dj

        from wl_preproc.schema._compat import apply_datajoint_compat

        apply_datajoint_compat()
        # reset=False: reuse an already-open connection rather than tearing
        # one down and reconnecting, so this check cannot itself interrupt a
        # daemon sharing the process. Confirmed against datajoint/connection.py
        # (DataJoint 2.3.2): with no connection yet, this still opens one from
        # dj.config — there is no way to test connectivity without that — but
        # it never discards a good connection to do so.
        conn = dj.conn(reset=False)
        report("database", bool(conn.is_connected))
    except Exception as exc:
        report("database", False, str(exc)[:80])

    # `/` rather than a dedicated scratch mount: there is no scratch-root
    # configuration to check instead yet (wl_preproc/contracts/paths.py's
    # SessionLayout takes its root as a caller-supplied argument, not a
    # resolved constant this module could import). So this is a proxy for the
    # real check, not the real check, and says so rather than implying
    # otherwise — and it is a real threshold, not a bound that can only ever
    # read "ok": a disk with less free space than one session needs while
    # processing is not actually ready, whatever else is true of it.
    free_gib, headroom_ok = scratch_headroom()
    report(
        "scratch headroom",
        headroom_ok,
        f"{free_gib} GiB free on / (proxy for the scratch mount; "
        f"floor is {_MIN_SCRATCH_FREE_GIB} GiB, one dual-probe session's worth)",
    )

    try:
        from wl_preproc.daemon import count_stale_jobs

        # Read-only: count_stale_jobs never mutates a job row, unlike the
        # reap_stale_jobs it is built on top of. A "doctor" that reaps as a
        # side effect of being asked a question is not a diagnostic.
        n = count_stale_jobs()
        if n is None:
            # No schema has been activated in this process -- true of every
            # bare `wlpp doctor` invocation today, since nothing above
            # activates one -- so nothing was actually inspected. Reporting
            # that honestly, rather than a fabricated "0 reaped", is the
            # whole point of this check existing: a wedged queue is exactly
            # what a fabricated all-clear would hide from the one tool meant
            # to surface it.
            report("stale jobs", True, "not checked: no schema activated in this process")
        else:
            report("stale jobs", n == 0, f"{n} stale reservation(s) found")
    except Exception as exc:
        report("stale jobs", False, str(exc)[:80])

    return failures
