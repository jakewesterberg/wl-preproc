import json
import subprocess
import sys

from wl_preproc.cli.main import EXPORTED_MODELS, export_schemas, main


def test_exports_one_file_per_model(tmp_path):
    written = export_schemas(tmp_path)
    assert {p.name for p in written} == {f"{name}.json" for name in EXPORTED_MODELS}


def test_exported_files_are_valid_json_schema(tmp_path):
    for path in export_schemas(tmp_path):
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert "properties" in schema
        assert schema["title"]


def test_sidecar_schema_is_exported_for_the_camera_project(tmp_path):
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "behavior_camera_sidecar.json").read_text())
    assert "video_files" in schema["properties"]


def test_job_request_schema_is_exported_for_wl_works(tmp_path):
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "job_request.json").read_text())
    assert "idempotency_key" in schema["properties"]


def test_syncbox_header_schema_comes_from_wl_sync(tmp_path):
    """The sync box owns its log format; we re-export it so wl.works has one
    place to fetch every contract."""
    export_schemas(tmp_path)
    schema = json.loads((tmp_path / "syncbox_log_header.json").read_text())
    assert "gpio_map" in schema["properties"]


def test_export_is_deterministic(tmp_path):
    """Committed schemas must not churn between runs, or every export shows a diff."""
    first = (tmp_path / "a")
    second = (tmp_path / "b")
    export_schemas(first)
    export_schemas(second)
    for name in EXPORTED_MODELS:
        assert (first / f"{name}.json").read_text() == (second / f"{name}.json").read_text()


def test_cli_writes_to_requested_directory(tmp_path):
    assert main(["schemas", "export", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "session_manifest.json").exists()


def test_cli_unknown_command_returns_nonzero():
    assert main(["nonsense"]) != 0


def test_module_is_runnable_with_dash_m(tmp_path):
    """Calling main() directly does not prove the entry point works. Without a
    __main__ guard, `python -m` imports the module and exits silently having
    done nothing."""
    result = subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", "schemas", "export", "--out", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "session_manifest.json").exists()
