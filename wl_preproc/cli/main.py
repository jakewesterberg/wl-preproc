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

from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.protocol import HealthResponse, JobRequest
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "session_manifest": SessionManifest,
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
    generate.add_argument("--profile", choices=["ci", "benchmark"], default="ci")

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
        from wl_preproc.synth.recipe import BENCHMARK_RECIPE, CI_RECIPE
        from wl_preproc.synth.session import generate_session

        recipe = CI_RECIPE if args.profile == "ci" else BENCHMARK_RECIPE
        args.out.mkdir(parents=True, exist_ok=True)
        truth = generate_session(args.out, recipe)
        print(f"{args.out / recipe.session_id}: {len(truth.trials)} trials")
        return 0

    return 2


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    raise SystemExit(main())
