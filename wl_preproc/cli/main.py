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
import datetime
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


def _session_key_from_dir(session_dir: Path) -> dict:
    """`(subject, session_datetime)` for the session at `session_dir`, read
    from its own manifest.

    `--session` names a session DIRECTORY for `archive`, `reclaim` and `hold`
    alike, the same thing `archive_session`'s own first parameter takes --
    not a bare id. `wl_sync.session.SessionId` (what a bare id like
    "2027-03-14_01" parses to) carries only a date and an index, never a
    subject or a time-of-day, so a string in that shape alone cannot resolve
    to the `(subject, session_datetime)` a database row is keyed on; the
    directory's manifest already carries both.

    Built through `manifest_session_key` -- the identical derivation
    `ingest/landing.py::land_session` uses to write the `pipeline.Session`
    row in the first place -- rather than re-deriving the two fields here, so
    a session identified this way and one already landed by `wlpp ingest`
    resolve to the exact same row rather than two keys that merely look
    alike.
    """
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest.landing import manifest_session_key

    manifest = SessionManifest.from_yaml(
        (session_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    return manifest_session_key(manifest)


def _staged_entries(prefix: str = DEFAULT_PREFIX) -> list[TapeEntry]:
    """Verified artifacts, shaped for `staging_manifest`.

    The "every file verified" predicate this wraps is NOT re-derived here --
    it lives once, at `cli/report.py::_verified_archives`, which this design
    spec section 3.2's report section and this command both need identically
    (a session `tape-manifest` stages for a cartridge and a session whose rig
    may clear its copy are the same fact, read for two different audiences).
    `main.py` already imports from `report.py` for the `report` subcommand
    below, so this adds no new edge, just a second name crossing it.
    """
    from wl_preproc.archive.tape import TapeEntry
    from wl_preproc.cli.report import _verified_archives

    return [
        TapeEntry(
            session_id=f"{row['subject']} @ {row['session_datetime']:%Y-%m-%d %H:%M:%S}",
            artifact_path=row["archive_path"],
            bytes=row["compressed_bytes"],
            manifest_digest=row["manifest_digest"],
        )
        for row in _verified_archives(prefix=prefix)
    ]


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

    archive_p = subparsers.add_parser("archive", help="compress and verify a session")
    archive_p.add_argument("--session", required=True, help="path to the session directory")
    archive_p.add_argument("--nas-root", required=True, type=Path)
    archive_p.add_argument("--host", required=True)
    archive_p.add_argument("--share", required=True)
    archive_p.add_argument("--prefix", default=DEFAULT_PREFIX)

    reclaim_p = subparsers.add_parser(
        "reclaim", help="preview whether a session's scratch copy may be freed"
    )
    reclaim_p.add_argument("--session", required=True, help="path to the session directory")
    # `--no-dry-run` + `--confirm`, not the brief's own `--dry-run` (Controller
    # ruling B): `--dry-run store_true default=True` can never be turned off
    # -- passing the flag sets True, omitting it leaves the default True.
    # This is the same shape `delete` already uses (cli/main.py, `delete`
    # parser above) so the two guardrails teach one convention, not two.
    reclaim_p.add_argument("--no-dry-run", action="store_true")
    reclaim_p.add_argument("--confirm", default=None)
    reclaim_p.add_argument("--prefix", default=DEFAULT_PREFIX)

    hold_p = subparsers.add_parser("hold", help="block or force reclamation")
    hold_p.add_argument("--session", required=True, help="path to the session directory")
    hold_p.add_argument("--verdict", choices=("hold", "force"), required=True)
    hold_p.add_argument("--actor", required=True)
    hold_p.add_argument("--reason", required=True)
    hold_p.add_argument("--prefix", default=DEFAULT_PREFIX)

    tape_p = subparsers.add_parser("tape-manifest", help="list sessions staged for tape")
    # Absent from the brief's own Step 3 snippet, which reads `args.prefix`
    # in the `tape-manifest` dispatch branch with no `add_argument` for it
    # anywhere -- the identical `AttributeError`-on-first-run shape
    # Controller ruling C names for `archive`'s `--nas-root`/`--host`/
    # `--share`, just not one of the five rulings that already lists it.
    tape_p.add_argument("--prefix", default=DEFAULT_PREFIX)

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

    if args.group == "archive":
        from wl_preproc.archive.stage import archive_session
        from wl_preproc.ingest import landing
        from wl_preproc.schema import archive as archive_schema

        session_dir = Path(args.session)
        # Key first, before `archive_session` runs: the NAS publish path
        # below is namespaced by subject, which the CLI knows before
        # publishing happens, not only after.
        key = _session_key_from_dir(session_dir)
        # Namespaced by subject -- review found that `archive_session`'s own
        # publish path (`archive/stage.py`: `nas_root / f"{session_dir.name}
        # .zarr"`) is a bare `SessionId` (date + index, `wl_sync.session`),
        # which carries no subject at all. Reproduced directly against a
        # real MySQL container: two different subjects sharing one session
        # id -- a real, reachable state; nothing anywhere enforces that
        # session ids are subject-scoped -- published to the IDENTICAL NAS
        # path, the second silently `rmtree`-ing and replacing the first,
        # with both `ArchiveArtifact` rows left recording that same path.
        # That defeats the settled triple's whole stated purpose (Controller
        # ruling C: "so another agent can open the file from elsewhere").
        # Fixed entirely at this call site rather than in `archive/stage.py`
        # (outside this task's assigned files, and inherited from Task 5):
        # passing a per-subject subdirectory as `nas_root` makes stage.py's
        # own unchanged logic publish to a unique path with no change there
        # at all. `archive_path` below is still computed relative to the
        # SHARE root (`args.nas_root`), not this nested one, so it stays
        # "relative to the share" exactly as ruling C requires -- it just
        # now includes the subject as its first path component.
        nas_root_for_session = args.nas_root / key["subject"]
        outcome = archive_session(session_dir, nas_root_for_session, args.host, args.share)
        for verdict in outcome.verdicts:
            if not verdict.matched:
                print(f"MISMATCH {verdict.relative_path}")
        print("verified" if outcome.all_matched else "NOT verified")

        archive_schema.activate(prefix=args.prefix)
        # `archive_session` has ALREADY overwritten the NAS artifact
        # unconditionally by this point, regardless of `all_matched`
        # (`archive/stage.py`: publish -- rmtree the old `published`, copy
        # the new one -- runs before the confirm/all_matched check, not
        # after it). So any PRIOR `ArchiveArtifact` row for this session now
        # describes bytes that no longer exist on the NAS, whether or not
        # THIS run verified. Deleted here, before the `all_matched` branch
        # below, so "no row" always honestly means "not archived" -- never
        # "was archived once, may or may not still be" (review, Important:
        # "the failure path leaves the database describing bytes that no
        # longer exist").
        #
        # `.delete(prompt=False)`, not `replace=True` on the insert below.
        # DataJoint declares every foreign key `ON DELETE RESTRICT`
        # (`datajoint/declare.py`), so a plain `REPLACE INTO` on
        # `ArchiveArtifact` -- MySQL's REPLACE is DELETE-then-INSERT -- while
        # its own `ArchiveVerification` children still exist from a PRIOR
        # run raises `IntegrityError`. Reproduced directly against a real
        # MySQL container with this schema (review, CRITICAL): a second
        # `wlpp archive` over an already-archived session got as far as
        # re-publishing a genuinely new artifact to the NAS, then crashed on
        # exactly this insert -- leaving the database recording the FIRST
        # run's digest for bytes the second run had already deleted, so
        # `manifest_digest(published) != ArchiveArtifact.manifest_digest`
        # afterward. `.delete()` cascades through the real dependency graph
        # instead (confirmed directly: deleting `ArchiveArtifact & key` also
        # removes its `ArchiveVerification` rows), which a bare `REPLACE
        # INTO` cannot do under `ON DELETE RESTRICT`. `prompt=False` is
        # required explicitly, not left to `dj.config["safemode"]`'s
        # default: that default is `True` outside this project's own test
        # fixtures (which set it `False` for the whole suite), and an
        # interactive y/n confirmation would hang a non-interactive cron/
        # systemd invocation of this command forever.
        (archive_schema.ArchiveArtifact & key).delete(prompt=False)

        if not outcome.all_matched:
            return 1

        # `ArchiveVerification` rows are written here too, alongside
        # `ArchiveArtifact`, though Controller ruling C's own text names
        # only the latter explicitly ("write the row only when outcome.
        # all_matched" -- singular). Before this task, nothing in this
        # codebase wrote either table: confirmed by grepping
        # `ArchiveVerification.insert` across wl_preproc/ and tests/ before
        # writing this -- the only hits were Task 7's own `tests/archive/
        # test_reclaim.py::_archive_and_verify` helper (pre-existing, not
        # this task's) and this task's own `tests/cli/test_archive_cli.py`.
        # `ArchiveVerification -> ArchiveArtifact` (`schema/archive.py`)
        # means there is no row shape that could record one without the
        # other anyway. Leaving it unwritten would make `reclaim_conditions`
        # 's `every_file_verified` condition (`archive/reclaim.py`) and
        # `_staged_entries` below fail closed forever, on every session --
        # the exact "tape-manifest prints an empty manifest when artifacts
        # exist" failure this task's own brief warns to catch with a real
        # test rather than trust.
        archive_path = str(outcome.artifact_path.relative_to(args.nas_root))
        # One instant reused for every row this call writes, not a fresh
        # `datetime.now()` per row: `StoreResult` (`archive/store.py`) carries
        # no timestamp of its own -- compression already finished by the time
        # this line runs -- so "now" is this pipeline's best honest record of
        # when the artifact was CONFIRMED good, the same moment `stage.py`'s
        # own sentinel is stamped.
        now = landing.to_naive_utc(datetime.datetime.now(datetime.UTC))
        # No `replace=True` needed here (or below): the prior row for this
        # key, if any, was just deleted above, so this is always a fresh
        # insert now.
        archive_schema.ArchiveArtifact.insert1(
            {
                **key,
                "archive_host": args.host,
                "archive_share": args.share,
                "archive_path": archive_path,
                "codec": outcome.store.codec,
                "clevel": outcome.store.clevel,
                "compressed_bytes": outcome.store.compressed_bytes,
                "manifest_digest": outcome.store.manifest_digest,
                "compressed_at": now,
            }
        )
        archive_schema.ArchiveVerification.insert(
            [
                {
                    **key,
                    "relative_path": verdict.relative_path,
                    "expected_blake3": verdict.expected,
                    "actual_blake3": verdict.actual,
                    "matched": 1 if verdict.matched else 0,
                    "verified_at": now,
                }
                for verdict in outcome.verdicts
            ]
        )
        print(
            f"archived: {key['subject']} @ {key['session_datetime']} -> "
            f"{args.host}:{args.share}/{archive_path}"
        )
        return 0

    if args.group == "reclaim":
        from wl_preproc.archive import reclaim as archive_reclaim
        from wl_preproc.archive.verify import _expected_digests

        session_dir = Path(args.session)
        key = _session_key_from_dir(session_dir)
        # `_expected_digests` (package-internal, underscored) rather than a
        # second walk of the DONE markers: it is the one place this
        # repository counts a session's expected files (`archive/verify.py`),
        # and re-deriving the count a second way here risks silently
        # disagreeing with what `verify_store` itself checked against.
        expected_file_count = len(_expected_digests(session_dir))
        conditions = archive_reclaim.reclaim_conditions(
            key, expected_file_count, prefix=args.prefix
        )

        print(f"reclaim preview for session {key['subject']} @ {key['session_datetime']}:")
        # Every condition, not merely the blocking ones -- design spec
        # section 5.2's own words: "A named list, not a boolean." (Review
        # round: an earlier version of this comment misquoted this as "a
        # NAMED LIST, not a verdict", a quotation that appears in no source
        # -- exactly the falsehood class this project's own CLAUDE.md
        # already names once.) The same reason `blocking()` alone is not
        # enough for `wlpp reclaim`'s own preview: a reader must be able to
        # tell "this passed" from "this was never evaluated", which only
        # printing every row can show.
        for condition in conditions:
            status = "OK" if condition.passed else "BLOCKED"
            detail = f" -- {condition.detail}" if condition.detail else ""
            print(f"  [{status}] {condition.name}{detail}")

        would_free = sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())
        verdict = "reclaimable" if archive_reclaim.reclaimable(conditions) else "NOT reclaimable"
        print(f"\n{verdict} -- would free {would_free} bytes from {session_dir} if it were.")

        if not args.no_dry_run:
            print("\nthis was a DRY RUN — nothing was freed.")
            print("re-run with --no-dry-run --confirm <session> to proceed.")
            return 0
        if args.confirm != args.session:
            # "session path", not "session id": `--session` names a
            # directory here (this file's own `--session` help text says so),
            # never a bare id -- review round: an earlier version of this
            # message copied `wlpp delete`'s wording verbatim, which is
            # accurate for THAT command's own `--session` but not this one's.
            print("\nrefusing: --confirm must repeat the session path exactly.")
            return 2
        # Controller ruling A: reclaim previews and deletes nothing in this
        # build, on PURPOSE, regardless of --no-dry-run/--confirm -- not an
        # oversight to fix later. Rehydration is not in this plan yet -- the
        # PLAN's own "Not in this plan" section (`docs/superpowers/plans/
        # 2026-08-27-archival-and-compression.md`, "Not in this plan"):
        # "Rehydration -- decompress-to-scratch. [Parent design spec] §8.4
        # names it as the path that makes reclamation safe, and it is the
        # natural next plan." (Review round: an earlier version of this
        # comment attributed that quote to "design spec section 8.4" of
        # THIS archival design document, which has no such section -- its
        # own `## 8` is "Schema", with no subsections at all, and every
        # "§8.4"/"§8.5" this document itself uses names the PARENT spec,
        # e.g. its own section 5's title, "Reclamation, and the reversal of
        # §8.5".) Freeing a session's only fast copy with no built path back
        # is the identical loss `wlpp delete`'s own guardrail refuses a few
        # branches above, for the same reason.
        print(
            "\nthis build never performs a real reclamation: rehydration "
            "(decompress-to-scratch), the path that makes freeing scratch "
            "safe to reverse, is not built yet -- so the preview above is "
            "as far as this command goes."
        )
        return 0

    if args.group == "hold":
        from wl_preproc.ingest import landing
        from wl_preproc.schema import archive as archive_schema

        session_dir = Path(args.session)
        key = _session_key_from_dir(session_dir)
        archive_schema.activate(prefix=args.prefix)
        held_at = landing.to_naive_utc(datetime.datetime.now(datetime.UTC))
        archive_schema.ReclamationHold.insert1(
            {
                **key,
                "held_at": held_at,
                "actor": args.actor,
                "verdict": args.verdict,
                "reason": args.reason,
            }
        )
        print(
            f"{args.verdict}: {key['subject']} @ {key['session_datetime']} "
            f"recorded by {args.actor} at {held_at:%Y-%m-%d %H:%M:%S} -- {args.reason}"
        )
        return 0

    if args.group == "tape-manifest":
        from wl_preproc.archive.tape import staging_manifest

        print(staging_manifest(_staged_entries(prefix=args.prefix)))
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
