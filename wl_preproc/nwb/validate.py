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


# nwbinspector's importances at CRITICAL or above: PYNWB_VALIDATION is a
# failure of the NWB schema, and ERROR a file pynwb cannot read (the final
# review's I3). Any one makes a file `invalid`.
BLOCKING = ("ERROR", "PYNWB_VALIDATION", "CRITICAL")
# ...except an ERROR that one of the inspector's own checks raised, which
# says nothing about the file: nwbinspector 0.7's column checks index row 0
# and raise on every empty table. It is kept with the other findings.
_CHECK_RAISED = "During evaluation of "


def blocks(finding: dict) -> bool:
    """Whether a finding makes the file `invalid`."""
    if finding["importance"] == "ERROR" and finding["check"].startswith(_CHECK_RAISED):
        return False
    return finding["importance"] in BLOCKING


def n_critical(findings: list[dict]) -> int:
    """How many findings make the file `invalid`: those at CRITICAL
    importance or above, less the inspector's own crashed checks."""
    return sum(1 for finding in findings if blocks(finding))
