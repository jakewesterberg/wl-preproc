"""wlpp entry point.

`wlpp schemas export` writes JSON Schema for every frozen contract. wl.works
builds its half of the protocol against these, and its 18b tests run against a
fake wl-preproc — which is only possible if the contract is machine-readable.
The behaviour-camera project builds against the sidecar schema the same way.

The sync box log header is re-exported from wl-sync rather than redefined, so
there is one place to fetch every contract and only one definition of each.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pydantic import BaseModel
from wl_sync.log import SyncBoxLogHeader

from wl_preproc.contracts.done import DoneMarker
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.protocol import HealthResponse, JobRequest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

# `wl_preproc.schema.__init__` is deliberately import-cheap (a constant and
# nothing else), so this costs no datajoint import for `wlpp schemas export`.
from wl_preproc.schema import DEFAULT_PREFIX

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "session_manifest": SessionManifest,
    "done_marker": DoneMarker,
    "behavior_camera_sidecar": BehaviorCameraSidecar,
    "syncbox_log_header": SyncBoxLogHeader,
    "health_response": HealthResponse,
    "job_request": JobRequest,
}


def export_schemas(out_dir: Path) -> list[Path]:
    """Write one JSON Schema per contract. Deterministic: sorted keys and a
    stable indent, so a committed export does not churn between runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in EXPORTED_MODELS.items():
        path = out_dir / f"{name}.json"
        schema = model.model_json_schema()
        schema.setdefault("title", model.__name__)
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def tcp_port(value: str) -> int:
    """`--port`'s `type=`: an int in 0-65535, refused by argparse at parse
    time if it is not.

    Named `tcp_port` because argparse puts a `type=` callable's `__name__`
    into its own message for a non-integer ("invalid tcp_port value: 'x'").

    Without this, `--port 99999` and `--port -1` both reached
    `socket.bind()` four frames inside `socketserver` and came out as
    `OverflowError: bind(): port must be 0-65535.` plus a traceback and exit
    1 — a plausible typo in the very systemd unit the `--port`-required
    ruling exists to protect, answered with an interpreter dump. `0` stays
    legal: it asks the OS for an ephemeral port, which is what the responder
    tests bind with.
    """
    port = int(value)  # argparse turns a ValueError here into its own message
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is not a TCP port (must be 0-65535)")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wlpp")
    subparsers = parser.add_subparsers(dest="group", required=True)

    schemas = subparsers.add_parser("schemas", help="contract schema tools")
    schemas_sub = schemas.add_subparsers(dest="action", required=True)
    export = schemas_sub.add_parser("export", help="write JSON Schema for every contract")
    export.add_argument("--out", default="docs/schemas", type=Path)

    synth = subparsers.add_parser("synth", help="synthetic session tools")
    synth_sub = synth.add_subparsers(dest="action", required=True)
    generate = synth_sub.add_parser("generate", help="write a synthetic session")
    generate.add_argument("--out", required=True, type=Path)
    # Choices come from RECIPES, not a literal list. They were two copies until
    # Phase 1c-4, and they drifted the moment a fourth recipe was added: "eye"
    # existed, `generate_session` handled it, and the CLI rejected it as an
    # invalid choice. Importing here costs an import of `recipe` at parser
    # construction, which is pydantic but not NumPy — the lazy import below is
    # what keeps the generator's real weight off `wlpp schemas export`.
    from wl_preproc.synth.recipe import RECIPES

    generate.add_argument("--profile", choices=sorted(RECIPES), default="ci")

    subparsers.add_parser("doctor", help="check this host's readiness")

    delete = subparsers.add_parser("delete", help="delete a session's rows from a stage down")
    delete.add_argument("--session", required=True)
    delete.add_argument("--from-stage", required=True)
    delete.add_argument("--no-dry-run", action="store_true")
    delete.add_argument("--confirm", default=None)

    daemon_p = subparsers.add_parser("daemon", help="run the populate daemon once")
    # DEFAULT_PREFIX, not a second literal: the prefix carries its own
    # separator and a copy here is exactly how `wlpplab` would come back.
    daemon_p.add_argument("--prefix", default=DEFAULT_PREFIX)

    ingest_parser = subparsers.add_parser("ingest", help="scan a storage root once")
    ingest_parser.add_argument("--root", required=True, help="directory holding session dirs")
    ingest_parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    ingest_parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip checksum verification; records integrity as 'skipped' rather than "
        "claiming a check that did not run",
    )

    report_parser = subparsers.add_parser("report", help="write the daily status report")
    report_parser.add_argument("--root", required=True, help="directory holding session dirs")
    report_parser.add_argument("--out", default="/var/lib/wlpp/reports")
    report_parser.add_argument("--prefix", default=DEFAULT_PREFIX)

    responder_parser = subparsers.add_parser(
        "responder", help="run the HTTP responder wl.works polls (design spec section 8/9)"
    )
    # No default -- controller ruling for Task 9: the port must be stated
    # identically in the systemd unit, the protocol document (Task 10) and
    # whatever wl.works is configured with, and a default invites two of
    # those three to disagree silently.
    # `type=tcp_port`, not `type=int`: see that function. An out-of-range
    # port is refused here, by argparse, in argparse's own words -- not
    # forty lines later as an OverflowError traceback out of socketserver.
    responder_parser.add_argument(
        "--port", required=True, type=tcp_port, help="TCP port to bind (0-65535)"
    )
    responder_parser.add_argument("--root", required=True, help="directory holding session dirs")
    responder_parser.add_argument("--prefix", default=DEFAULT_PREFIX)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # `exc.code or 2` was wrong for the one status argparse raises that is
        # not a failure: `--help` and `--version` exit 0, and `0 or 2` is 2, so
        # EVERY subcommand's --help returned 2. Enough to fail a wrapper or a CI
        # step written as `wlpp --help || exit 1`, and enough to make the
        # protocol document's exit-code table describe a bug. `None` (a bare
        # `raise SystemExit`) still means 2; argparse's own usage errors already
        # carry 2 themselves.
        return 2 if exc.code is None else int(exc.code)

    if args.group == "schemas" and args.action == "export":
        for path in export_schemas(args.out):
            print(path)
        return 0

    if args.group == "synth" and args.action == "generate":
        # Imported lazily: the generator pulls in NumPy, and `wlpp schemas
        # export` has no reason to pay for it.
        from wl_preproc.synth.recipe import RECIPES
        from wl_preproc.synth.session import generate_session

        recipe = RECIPES[args.profile]
        args.out.mkdir(parents=True, exist_ok=True)
        truth = generate_session(args.out, recipe)
        print(f"{args.out / recipe.session_id}: {len(truth.trials)} trials")
        return 0

    if args.group == "doctor":
        from wl_preproc.cli.doctor import run_checks

        failures = run_checks()
        return 1 if failures else 0

    if args.group == "delete":
        from wl_preproc.cli.deleting import plan_cascade

        cascade = plan_cascade(args.session, args.from_stage)
        print(f"cascade from {args.from_stage} for session {args.session}:")
        for line in cascade:
            print(f"  {line}")
        if not args.no_dry_run:
            print("\nthis was a DRY RUN — nothing was deleted.")
            print("re-run with --no-dry-run --confirm <session-id> to proceed.")
            return 0
        if args.confirm != args.session:
            print("\nrefusing: --confirm must repeat the session id exactly.")
            return 2
        print("\nthis build never performs a real delete (see the design spec, "
              "section 10): the cascade above is preview-only.")
        return 0

    if args.group == "daemon":
        from wl_preproc.daemon import run_once

        report = run_once(prefix=args.prefix)
        print(f"populated: {report['populated']}")
        print(f"stale jobs reaped: {report['stale_jobs_reaped']}")
        if report["errors"]:
            print("errors:")
            for err in report["errors"]:
                print(f"  {err}")
        else:
            print("errors: none")
        return 0

    if args.group == "ingest":
        # `Path` is not re-imported here: it is already a module-level import
        # above (line 17), used unconditionally by `schemas export`'s own
        # `--out` argument. A second `from pathlib import Path` inside this
        # branch would make the compiler treat `Path` as local to the whole
        # `main()` function rather than shadowing it only here -- Python
        # decides a name's scope for an entire function from every assignment
        # anywhere in its body, not per-branch -- which raises
        # `UnboundLocalError` on the *earlier*, unconditional
        # `export.add_argument("--out", ..., type=Path)` call before this
        # branch is ever reached, for every subcommand, not just `ingest`.
        # Found by running the full suite after adding this branch: every CLI
        # guardrail test failed with exactly that traceback.
        from wl_preproc.ingest.watcher import Outcome, scan_once

        result = scan_once(Path(args.root), prefix=args.prefix, verify=not args.no_verify)
        # Outcomes are printed FIRST, unconditionally, before root_error is
        # even inspected: a mid-walk fault (root.iterdir()'s own next() call
        # raising after already yielding some children -- see
        # _candidate_dirs) still leaves scan_once processing every candidate
        # collected before the fault, real Ingestion/Quarantine rows and
        # all. Returning early on root_error used to discard every one of
        # those from this report -- real work that happened, silently
        # dropped from the one place an operator would see it.
        for session_dir, outcome in sorted(result.outcomes.items()):
            print(f"  [{outcome}] {session_dir}")
        # A refused scan WITH WAITING SESSIONS admitted nothing at all --
        # every one of them was turned away by scratch headroom
        # (watcher.refuses_new_sessions), never evaluated -- and exiting 0
        # here would read exactly like a scan that ingested cleanly to
        # anything watching this command's exit code (a cron wrapper, a
        # systemd unit). Checked independently of root_error below: the two
        # name different faults (tight scratch vs. an unreadable root), and
        # either one, or both at once, must be non-zero.
        #
        # A root with NO waiting candidates stays quiet here on purpose,
        # even when scratch is tight: `outcomes` is then empty regardless of
        # `refuses_new_sessions`, so there is nothing to report turning
        # away, and `responder/health.py` already surfaces low headroom as
        # `degraded` on every wl.works poll (design spec section 8.4's "and
        # alerts" half) -- a second alerting path here would be a second
        # definition of "is this host degraded", which `_featured_key`'s own
        # docstring says this project has already found in four separate
        # shapes.
        refused = Outcome.REFUSED in result.outcomes.values()
        if refused:
            print(
                f"error: {args.root} was not scanned: scratch headroom is below the "
                "floor or could not be measured; refusing new sessions"
            )
        if result.root_error is not None:
            # An unreadable, missing, or mistyped --root produces the same
            # empty outcomes dict a genuinely empty root does -- exit 0, no
            # output either way, unless this is checked. A typo, an
            # unmounted NAS, or an ACL slip on the storage root silently
            # reporting success is exactly "a session simply never appears"
            # arriving through the front door instead of a stalled transfer.
            print(f"error: {args.root} was not fully scanned: {result.root_error}")
        if refused or result.root_error is not None:
            return 1
        return 0

    if args.group == "report":
        # `Path` is not re-imported here, for the identical reason the
        # `ingest` branch above states in full: it is already a module-level
        # import (line 17), used unconditionally by `schemas export`'s own
        # `--out` argument, and a second `from pathlib import Path` anywhere
        # in main()'s body would make the compiler treat `Path` as local to
        # the WHOLE function rather than just this branch -- raising
        # UnboundLocalError at the earlier, unconditional
        # `export.add_argument("--out", ..., type=Path)` call for every
        # subcommand, not just `report`. This is exactly the defect Task 8
        # found and fixed for `ingest`; the brief for this task repeated it.
        from wl_preproc.cli.report import write_report

        path = write_report(Path(args.out), Path(args.root), prefix=args.prefix)
        print(path.read_text(), end="")
        return 0

    if args.group == "responder":
        # `Path` is not re-imported here, for the identical reason the
        # `ingest` and `report` branches above both state in full: it is
        # already a module-level import (line 17 now that `os` was added
        # above it), used unconditionally by `schemas export`'s own
        # `--out` argument. A second `from pathlib import Path` anywhere in
        # main()'s body makes the compiler treat `Path` as local to the
        # WHOLE function rather than just this branch, raising
        # `UnboundLocalError` at that earlier, unconditional call for every
        # subcommand, not just this one. That bug has already been
        # introduced twice in this project (Task 8's `ingest`, then
        # `report`); this branch does not add a third instance.
        #
        # `os.environ` is read HERE, at dispatch time, not at module import
        # time: a module-scope read would make the token unsettable by a
        # test, and by a systemd unit that sets it only after this module
        # has already been imported.
        token = os.environ.get("WLPP_RESPONDER_TOKEN")
        if not token:
            # `if not token`, never `if token is None`. `os.environ.get`
            # returns `""` for `WLPP_RESPONDER_TOKEN=` in a systemd unit --
            # a realistic way to get here -- and an empty string passes
            # `str.isascii()` exactly like any other ASCII string, so
            # `token is None` would let it through into a responder that
            # then 401s every request, including the correct one, for as
            # long as the process runs. One message covers both an unset
            # and an empty variable, naming it exactly once.
            print(
                "error: WLPP_RESPONDER_TOKEN is not set (or is empty); "
                "refusing to start a responder with no token -- there is "
                "no default."
            )
            return 2

        # Fix round 2: `build_health` (via `gather_readings`) only reads
        # `root` on the request path, never at startup, so a typo'd --root
        # used to bind the port and serve happily -- failing per request,
        # forever, rather than once here. Same principle already applied to
        # the token and the port above: refuse to start rather than run
        # broken. `Path.is_dir()` is `False` for both a missing path and a
        # path that names a file, so this one check covers both -- and
        # Task 10 has just shipped a document telling an operator to write a
        # --root into a systemd unit, where a typo is exactly this mistake.
        #
        # Fix round 3: `not args.root` FIRST, and it is not redundant with
        # `is_dir()`. `Path("")` is `PosixPath('.')`, whose `.is_dir()` is
        # `True`, so `--root=` passed this check and served the working
        # DIRECTORY OF THE PROCESS -- measured: exit 1 (it reached `serve()`
        # and failed on the port), root handed to `serve()` == cwd. That is
        # the identical trap the token check three statements above exists
        # for and names in full: `ExecStart=... --root=${WLPP_ROOT}` with
        # `WLPP_ROOT` unset expands to exactly `--root=`, in the systemd
        # unit Task 10 shipped. An empty root is an unconfigured root, not
        # the working directory.
        #
        # `.` and `./` are legitimate and stay legitimate -- `not args.root`
        # is false for both, and `test_responder_accepts_a_relative_root`
        # pins that. `--root " "` was already correctly refused: a directory
        # named " " does not exist.
        root = Path(args.root)
        if not args.root or not root.is_dir():
            print(
                f"error: --root {args.root!r} does not exist or is not a "
                "directory; refusing to start a responder with a broken "
                "storage root."
            )
            return 2

        # Imported here, not at module level, for the same reason `daemon`/
        # `ingest`/`report`/`synth generate` above all do it locally:
        # `wl_preproc.responder.server` imports `wl_preproc.responder.jobs`,
        # which imports the schema layer and therefore DataJoint
        # transitively -- a cost `wlpp schemas export` has no reason to pay.
        from wl_preproc.responder.server import serve

        try:
            serve(args.port, token, root, prefix=args.prefix)
        except ValueError as exc:
            # `serve()` raises `ValueError` from inside `make_handler` for a
            # non-ASCII token, before any socket is bound (responder/
            # server.py's own docstring, "The configured token must be
            # ASCII") -- a real refusal, just not yet shaped like a CLI
            # error message. Caught here rather than left to propagate: an
            # operator reading a systemd journal gets this line, not a
            # traceback ending in a raise deep inside make_handler.
            print(f"error: refusing to start: {exc}")
            return 2
        except OSError as exc:
            # `OSError`, not just the `ValueError` above, because "a
            # ValueError traceback is not a CLI error message" (controller
            # override 3) is a principle and not a list of one exception
            # type -- and the commonest responder restart failure there is
            # lands here: `[Errno 48] Address already in use`, a systemd
            # restart racing the old process's socket, previously a raw
            # traceback ending in `socketserver.TCPServer.server_bind`. A
            # privileged port with no capability to bind it arrives the
            # same way.
            #
            # Exit 1, not the 2 the refusals above use: 2 in this CLI means
            # "refusing -- the invocation or configuration is wrong", and a
            # correct invocation hits this when something ELSE holds the
            # port. It tried and failed, which is `doctor`'s exit 1. The
            # port is named because it is what an operator has to go free.
            #
            # Fix round 2: the message below no longer claims "bind",
            # although in practice a bind failure is overwhelmingly what
            # reaches here. This `try` spans the whole of `serve()`, not
            # just `ThreadingHTTPServer.__init__`, and the rest of `serve()`
            # is not entirely inert: `socketserver.BaseServer.
            # _handle_request_noblock` does catch `OSError` from
            # `get_request()` and return, so a per-request accept failure
            # never reaches here, but `serve_forever()`'s own
            # `selector.select()` call is NOT similarly guarded -- an
            # `OSError` there (a listening socket's file descriptor going
            # bad from outside this process, say, after months of uptime)
            # would reach this handler too, long after any bind, and "could
            # not bind port N" would be a false description of it. Narrowing
            # the `try` to just the constructor would fix that precisely,
            # but costs a distinct exception type out of `serve()` or a
            # split in the CLI -- more surface than a very-low-probability
            # wrong noun deserves. The message says only what is true on
            # both paths: this port, the OS's own error text (errno
            # included), exit 1.
            print(f"error: responder failed on port {args.port}: {exc}")
            return 1
        # Reached only if `serve_forever()` returns, which it does only on an
        # explicit `shutdown()` no caller here issues; in practice this
        # process ends on a signal instead.
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
