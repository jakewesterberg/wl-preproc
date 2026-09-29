"""`nwbinspector` over a written file (parent spec section 8.1: "validated
with nwbinspector before publication"; design spec
`2026-09-28-nwb-builder-design.md` section 8). Its default configuration,
not DANDI's."""

from __future__ import annotations

from pathlib import Path


def inspect_file(path: Path) -> list[dict]:
    """Every finding, as a plain dict: `importance`, `check`, `message`,
    `object_type`, `location`."""
    from nwbinspector import inspect_nwbfile

    return [
        {
            "importance": message.importance.name,
            "check": message.check_function_name,
            "message": message.message,
            "object_type": message.object_type,
            "location": message.location or "",
        }
        for message in inspect_nwbfile(nwbfile_path=str(path))
    ]


def n_critical(findings: list[dict]) -> int:
    return sum(1 for finding in findings if finding["importance"] == "CRITICAL")
