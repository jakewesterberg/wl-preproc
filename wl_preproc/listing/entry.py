"""One landed session's entry in `GET /sessions` (design spec
`2026-10-01-session-listing-and-run-requests-design.md` sections 2.2 and 2.4).

**Gathered, then built.** `gather_all` reads the database into plain rows,
each table once for every session asked for; `build_entry` turns one
session's rows into its entry, with no database, so every flag is tested on
rows made by hand. The listing stage and `GET /sessions` both build entries
this way, and `session_entry` is the two for one session, so they cannot
disagree.

**A block belongs to the run its start lies in**, and a segment to every run
it overlaps: runs and segments do not align, since a bank change needs a
SpikeGLX restart and a run need not stop for one. A block's start is stored
as a MySQL FLOAT and a run's as a double, so they are compared by
`events/runs.py::starts_inside`, as the NWB builder compares them.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
from pathlib import Path

from wl_preproc.events.runs import starts_inside, stored_doubles

from wl_preproc.contracts.protocol import SessionEntry


@dataclasses.dataclass(frozen=True)
class SessionFacts:
    """Everything an entry is built from, as the tables hold it."""

    subject: str
    session_datetime: datetime.datetime
    session_name: str
    tier: str | None
    rejected: list[dict]  # {system, file_path, reason}
    runs: list[dict]  # core.Run rows
    records: dict[int, dict]  # run_number -> core.RunRecord row
    blocks: list[dict]  # trial.Block rows: block_id, block_start_time, block_stop_time
    block_attributes: dict[int, dict[str, str]]  # block_id -> {attribute_name: attribute_value}
    trial_counts: dict[int, int]  # block_id -> trials stored in it
    segments: list[dict]  # SpikeGLX core.Segment rows
    census: list[dict]  # ephys.ProbeCensus.Probe rows
    electrodes: dict[tuple[str, str], list[int]]  # (electrode_config_hash, probe_type) -> sorted electrodes
    strobed_runs: list[int]  # every RUN_START's run number, one per occurrence
    strobed_blocks: list[int]  # every BLOCK_START's block number, one per occurrence
    censused: frozenset[int]  # the segments the probe census has read
    rig_problems: list[str]  # what reading the rig's run record could not use (core.RunRecordProblem)


def _repeats(numbers: list[int]) -> list[tuple[int, int]]:
    return sorted((number, count) for number, count in collections.Counter(numbers).items() if count > 1)


def _flag(code: str, message: str, run_number: int | None = None, block_number: int | None = None) -> dict:
    return {"code": code, "message": message, "run_number": run_number, "block_number": block_number}


def build_entry(facts: SessionFacts) -> dict:
    """The entry, validated against `SessionEntry`, as JSON-ready data."""
    flags = []
    if not facts.runs:
        flags.append(_flag("waiting_for_run_markers",
                           "the recording has no measured run: waiting for the rig's run markers"))
    for number, count in _repeats(facts.strobed_runs):
        flags.append(_flag("repeated_run_number",
                           f"run number {number} appears {count} times in the recording; only the first is listed, "
                           "and the restarted runs wait for wl-xcon's XC-026", run_number=number))
    for number, count in _repeats(facts.strobed_blocks):
        flags.append(_flag("repeated_block_number",
                           f"block number {number} appears {count} times in the recording; only the first is "
                           "listed", block_number=number))
    for problem in facts.rig_problems:
        flags.append(_flag("rig_record_problem", f"the rig's run record: {problem}"))

    probes_of = collections.defaultdict(list)
    for part in sorted(facts.census, key=lambda part: (part["segment_barcode"], part["stream"])):
        config = (part["electrode_config_hash"], part["probe_type"])
        electrodes = facts.electrodes.get(config) if part["electrode_config_hash"] else None
        probes_of[part["segment_barcode"]].append({
            "stream": part["stream"], "serial": part["probe_serial"], "part_number": part["part_number"],
            "probe_type": part["probe_type"], "electrode_config_hash": part["electrode_config_hash"],
            "n_electrodes": None if electrodes is None else len(electrodes), "electrodes": electrodes,
            "imro_table": part["imro_table"], "problem": part["problem"],
        })
    segments = [
        {"segment_barcode": segment["segment_barcode"], "start_s": segment["start_s"], "end_s": segment["end_s"],
         "probes": probes_of[segment["segment_barcode"]] if segment["segment_barcode"] in facts.censused else None}
        for segment in sorted(facts.segments, key=lambda segment: segment["start_s"])
    ]

    runs, in_a_run = [], set()
    ordered_runs = sorted(facts.runs, key=lambda run: run["run_number"])
    for run in ordered_runs:
        start, stop = run["run_start_time"], run["run_stop_time"]
        record = facts.records.get(run["run_number"], {})
        spanned = [segment for segment in segments if segment["start_s"] < stop and segment["end_s"] > start]
        blocks = sorted((block for block in facts.blocks if starts_inside(block["block_start_time"], start, stop)),
                        key=lambda block: block["block_start_time"])
        listed_blocks = []
        in_a_run.update(block["block_id"] for block in blocks)
        for order, block in enumerate(blocks, start=1):
            attributes = facts.block_attributes.get(block["block_id"], {})
            listed_blocks.append({
                "block_number": block["block_id"], "block_in_run": order,
                "block_type": attributes.get("block_type"),
                "start_s": float(block["block_start_time"]), "end_s": float(block["block_stop_time"]),
                "closed": None if "closed" not in attributes else attributes["closed"] == "1",
                "n_trials": facts.trial_counts.get(block["block_id"], 0),
            })
        runs.append({
            "run_number": run["run_number"], "task_code": run["task_type"], "task": record.get("task"),
            "start_s": start, "end_s": stop, "closed": bool(run["closed"]),
            "stopped_because": record.get("stopped_because"), "stop_kind": record.get("stop_kind"),
            "segments": [segment["segment_barcode"] for segment in spanned], "blocks": listed_blocks,
        })
        if not listed_blocks:
            flags.append(_flag("run_without_block",
                               f"run {run['run_number']} recorded no block: it stopped before its first trial",
                               run_number=run["run_number"]))
        maps = collections.defaultdict(set)
        for segment in spanned:
            for probe in segment["probes"] or ():
                if probe["serial"] and probe["electrode_config_hash"]:
                    maps[probe["serial"]].add(probe["electrode_config_hash"])
        for serial in sorted(serial for serial, found in maps.items() if len(found) > 1):
            flags.append(_flag("bank_change_in_run",
                               f"run {run['run_number']} spans segments where probe {serial} records two site "
                               "maps: a bank changed inside the run", run_number=run["run_number"]))
        if record.get("task") is None:
            flags.append(_flag("task_unknown", f"run {run['run_number']} has no recorded task",
                               run_number=run["run_number"]))
        for block in listed_blocks:
            if block["block_type"] is None:
                flags.append(_flag("block_type_unknown", f"block {block['block_number']} has no recorded block type",
                                   run_number=run["run_number"], block_number=block["block_number"]))

    # A block in no run, when the session has runs: its run's RUN_START was
    # lost, or it was strobed outside a run. With no runs at all the session
    # is already waiting for its run markers, and every block is outside one.
    if facts.runs:
        for block in sorted(facts.blocks, key=lambda block: block["block_start_time"]):
            if block["block_id"] not in in_a_run:
                flags.append(_flag("block_outside_runs",
                                   f"block {block['block_id']} lies in no measured run: its run's RUN_START was "
                                   "lost, or it was strobed outside a run", block_number=block["block_id"]))

    entry = {
        "subject": facts.subject, "session_datetime": facts.session_datetime, "session_name": facts.session_name,
        "tier": facts.tier, "rejected_segments": sorted(facts.rejected, key=lambda row: (row["system"],
                                                                                          row["file_path"])),
        "runs": runs, "segments": segments,
        "probes": sorted({part["probe_serial"] for part in facts.census if part["probe_serial"]}),
        "flags": flags,
    }
    return SessionEntry.model_validate(entry).model_dump(mode="json")


def _session(row: dict) -> tuple[str, datetime.datetime]:
    return row["subject"], row["session_datetime"]


def _by_session(rows) -> collections.defaultdict:
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[_session(row)].append(row)
    return grouped


def gather_all(keys: list[dict]) -> dict[tuple[str, datetime.datetime], SessionFacts]:
    """The facts of each of `keys`' sessions that has landed, by `(subject,
    session_datetime)`, read from the database. Each table is read once for
    all of them: every daemon pass builds every listed session's entry, and
    reading each table once per session grew with the lab (the listing's
    M9)."""
    from wl_preproc.schema import core, ephys, ingest, pipeline, timebase

    keys = [{name: key[name] for name in ("subject", "session_datetime")} for key in keys]
    if not keys:
        return {}
    tiers = {_session(row): row["tier"] for row in (timebase.TimingProvenance & keys).proj("tier").to_dicts()}
    attributes = collections.defaultdict(lambda: collections.defaultdict(dict))
    for row in (pipeline.trial.Block.Attribute & keys).to_dicts():
        attributes[_session(row)][row["block_id"]][row["attribute_name"]] = row["attribute_value"]
    census = _by_session((ephys.ProbeCensus.Probe & keys).to_dicts())
    configs = sorted({(part["electrode_config_hash"], part["probe_type"])
                      for parts in census.values() for part in parts if part["electrode_config_hash"]})
    electrodes = collections.defaultdict(list)
    if configs:
        restriction = [{"electrode_config_hash": config_hash, "probe_type": probe_type}
                       for config_hash, probe_type in configs]
        for row in (ephys.ElectrodeConfig.Electrode & restriction).to_dicts():
            electrodes[(row["electrode_config_hash"], row["probe_type"])].append(int(row["electrode"]))

    def strobed(event_type: str, name: str) -> collections.defaultdict:
        rows = pipeline.event.Event.Attribute & keys & {"event_type": event_type, "attribute_name": name}
        numbers = collections.defaultdict(list)
        for row in rows.proj("attribute_value").to_dicts():
            numbers[_session(row)].append(int(row["attribute_value"]))
        return numbers

    # Only the blocks a trial is stored in: `aggr` otherwise keeps every block
    # through a LEFT JOIN, and `count(*)` counts its one unmatched row (the
    # M9 review's C1). A block with none is listed with 0.
    trials = collections.defaultdict(dict)
    for row in (pipeline.trial.Block & keys).aggr(pipeline.trial.BlockTrial, n_trials="count(*)",
                                                  exclude_nonmatching=True).to_dicts():
        trials[_session(row)][int(row["block_id"])] = int(row["n_trials"])
    rejected = _by_session((core.RejectedSegment & keys).to_dicts())
    runs = _by_session((core.Run & keys).to_dicts())
    records = _by_session((core.RunRecord & keys).to_dicts())
    blocks = _by_session(stored_doubles(pipeline.trial.Block & keys, "block_start_time", "block_stop_time"))
    segments = _by_session((core.Segment & keys & {"system": "spikeglx"}).to_dicts())
    strobed_runs, strobed_blocks = strobed("RUN_START", "run_number"), strobed("BLOCK_START", "block_id")
    censused = _by_session((ephys.ProbeCensus & keys).proj().to_dicts())
    rig_problems = _by_session((core.RunRecordProblem & keys).to_dicts(order_by="problem_number"))
    facts = {}
    for ingestion in (ingest.Ingestion & keys).proj("session_dir").to_dicts():
        session = _session(ingestion)
        mine = census.get(session, [])
        facts[session] = SessionFacts(
            subject=session[0],
            session_datetime=session[1],
            session_name=Path(ingestion["session_dir"]).name,
            tier=str(tiers[session]) if session in tiers else None,
            rejected=[{"system": row["system"], "file_path": row["file_path"], "reason": row["reason"]}
                      for row in rejected.get(session, [])],
            runs=runs.get(session, []),
            records={row["run_number"]: row for row in records.get(session, [])},
            blocks=blocks.get(session, []),
            block_attributes=dict(attributes.get(session, {})),
            trial_counts=trials.get(session, {}),
            segments=segments.get(session, []),
            census=mine,
            electrodes={config: sorted(electrodes[config]) for config in
                        {(part["electrode_config_hash"], part["probe_type"]) for part in mine
                         if part["electrode_config_hash"]}},
            strobed_runs=strobed_runs.get(session, []),
            strobed_blocks=strobed_blocks.get(session, []),
            censused=frozenset(int(row["segment_barcode"]) for row in censused.get(session, [])),
            rig_problems=[row["problem"] for row in rig_problems.get(session, [])],
        )
    return facts


def gather_facts(session_key: dict) -> SessionFacts:
    """One session's facts, read from the database. The session is found by
    the database, so its key may name its time in any form MySQL compares
    (the M9 review's M1)."""
    found = list(gather_all([session_key]).values())
    if len(found) != 1:
        raise LookupError(f"no landed session {session_key['subject']} at {session_key['session_datetime']}")
    return found[0]


def session_entry(session_key: dict) -> dict:
    """The session's entry as it stands now."""
    return build_entry(gather_facts(session_key))
