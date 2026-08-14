"""`wlpp doctor` — is this host ready to run the pipeline?"""

from __future__ import annotations

import shutil


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

    usage = shutil.disk_usage("/")
    report("scratch headroom", usage.free > 0, f"{usage.free // 2**30} GiB free")

    try:
        from wl_preproc.daemon import reap_stale_jobs

        report("stale jobs", True, f"{reap_stale_jobs(older_than_s=3600)} reaped")
    except Exception as exc:
        report("stale jobs", False, str(exc)[:80])

    return failures
