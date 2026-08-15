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
    generate.add_argument("--profile", choices=["ci", "benchmark", "stim"], default="ci")

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

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 2)

    if args.group == "schemas" and args.action == "export":
        for path in export_schemas(args.out):
            print(path)
        return 0

    if args.group == "synth" and args.action == "generate":
        # Imported lazily: the generator pulls in NumPy, and `wlpp schemas
        # export` has no reason to pay for it.
        from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE, STIM_RECIPE
        from wl_preproc.synth.session import generate_session

        recipes = {"ci": CI_RECIPE, "benchmark": BENCHMARK_RECIPE, "stim": STIM_RECIPE}
        recipe = recipes[args.profile]
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

    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
