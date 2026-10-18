# Carrying a session's kind, its display calibration and its accepted warnings

**wl-xcon's ask 5 of 2026-10-11** (`HANDOVER-wl-expcontroller.md`, item 5; its backlog XC-325): read
what its engine build B writes into every session's record, and carry it into the published
record. A session run in a non-experimental kind, or with an accepted warning, should be marked so;
"only carrying, not acting on, the warnings is asked".

**Status.**
- Chosen on 2026-10-14, the hardware unchanged, after sorting wl-xcon's open asks by what January
  needs: this one is written in every session from day one and can be built now. The design was
  approved in conversation the same day; the requester's decisions are in §0.
- Branch `feat/session-kind`, from `main` at `ae1d439`.

**The rig and compute machine are still unavailable.** Nothing here needs them.

---

## 0. The requester's decisions, 2026-10-14

1. **Carry only.** Nothing this pipeline does changes with a session's kind. wl.works, which fires
   each canonical NWB from the listing, and the analysts reading the files decide what a training
   or piloting session means.
2. **Carried into the database, the session listing and the NWB file with its description.** The
   listing's change is an amendment for wl.works to take (§4).
3. **Who accepted a warning does not go into the NWB file.** The database keeps it as wl-xcon wrote
   it; the listing gives wl.works the name and its own account id, never the token id. The NWB file
   already names the session's experimenter, as wl.works' request states it; that is wl.works'
   choice to publish, and this one stays in the database and the listing. *The question was first
   asked on the premise that the file names no person; it was asked again once corrected, and the
   answer stood.*
4. **New tables beside the run record, none changed**, so a database declared before them needs no
   `ALTER TABLE` (approach 1 of three: a column on `core.RunRecord` would need one on every
   database, and raw JSON in one table would spread the parsing over two consumers).

---

## 1. What wl-xcon writes

Read on wl-xcon's `main` at `b2c2850` (2026-10-12). Engine build B has been on its `main` since
2026-10-09 (its `docs/CHECKPOINT.md`).

- **`xcon/config.json`**, what is fixed for the session (`wl_xcon/taskd.py::_fixed_config`, lines
  704-720):
  - `session_kind`: `"training"`, `"piloting"` or `"recording"` (`wl_xcon/findings.py:24`,
    `SESSION_KINDS`), chosen at the session's open and fixed for it (engine spec §19.1);
  - `calibration`: the display's colour calibration the session opened with, or `null` when the
    rig's record would not load: `id` (text), `standard` (`true` for the sRGB default, which nobody
    measured), `measured_on` (`YYYY-MM-DD`, empty for the standard), and `cones`, how cone colours
    became light: the observer and its parameters, what converted, which luminance isoluminance
    held, and what a DKL number means (`wl_xcon/photometry.py`, `Calibration` and `cone_record`).
  - A `config.json` written before build B has neither field.
- **`xcon/runs.jsonl`**: each run's start row carries `calibration`, the id it ran against
  (`taskd.py:2145-2153`). `config.json` keeps the one the session opened with; a resume after the
  rig's calibration changed is not refused, so the two can differ.
- **`xcon/warnings.jsonl`**: one row per warning the session accepted, written as it is accepted
  (`wl_xcon/record.py`, `ACCEPTED_WARNINGS` and `SessionRecord.warning`): `code`, `detail` (its
  sentence), `accepted_in` (the kinds it is acceptable in), `session_kind`, `by`, `at` (the
  session's anchored wall clock, in seconds), `at_local`, `how` and `run`.
  - `how` is `"open"` (accepted at the session's open, `wl_xcon/service.py:893`), `"start"` (at a
    run's pre-flight, `taskd.py:2101`) or `"--accept-warnings"` (`wlx run`, `wl_xcon/cli.py:1863`).
  - `run` is `null` at the open and otherwise the 0-based index of the run about to start, the same
    `run` its rows in `runs.jsonl` carry (`taskd.py:2101-2102`).
  - `by` is `null` for nobody, `{"kind": "box", "name": ...}` for a name typed at the rig, or
    `{"kind": "member", "name", "account", "issuer", "token_id"}` for a person wl.works signed in
    (`wl_xcon/actor.py:61-80`).
  - A warning is accepted at most once per session; a reworded one is asked again (engine spec
    §19.3).

These instants are the rig's bookkeeping, not times on the recording's clock: the sync box stays
the timing ground truth (wl-xcon's XC-288 spec §3.11). Nothing here places them on session time.

---

## 2. Reading

**In the events stage** (`schema/events.py::populate_session`), where `xcon/runs.jsonl` is already
read once because the raw files are later archived.

- **A new reader, `events/rigsession.py`**, for `config.json`'s kind and calibration and for
  `warnings.jsonl`, by `events/rigruns.py`'s rules: read, never repaired; a line that is not a JSON
  object, or a warning row without a `code`, is reported by line and left out; a record whose
  `config.json` names another animal is not read at all.
- **`events/rigruns.py` gains each run's `calibration`** from its start row.
- **A warning's run is mapped to the measured run's number** through the start row with the same
  `run`: `run_in_session` where the row carries it, `run` + 1 before it, as `rigruns.py` already maps
  a run. A warning accepted at the open has none.
- **A kind outside the three is a problem**, reported, and stored as `null`. So is a `calibration`
  that is neither `null` nor an object.
- **A `config.json` without the fields** (written before build B) reads as kind and calibration
  `null`, meaning not recorded, and is not a problem.

## 3. Storage

Three new tables in `schema/core.py`, declared on activation; no existing table changes.

- **`core.SessionRecord`**, one row per session with a wl-xcon `config.json` (key: the session):
  `session_kind`, `calibration_id`, `calibration_standard`, `calibration_measured_on` and
  `calibration_cones` (`<blob>`, as given, as paramsets store their dicts), each nullable.
- **`core.RunCalibration`**, one row per measured run whose start row names a calibration (key: the
  run, as `core.RunRecord`'s): `calibration_id`.
- **`core.AcceptedWarning`**, one row per accepted warning (key: the session and `warning_number`,
  from 1 in the file's order): `code`, `detail`, `accepted_in` (the kinds, comma-joined),
  `session_kind`, `how`, `run_index` (wl-xcon's, null at the open), `run_number` (the measured
  run's, null at the open or when the run was not measured), `at`, `at_local`, and who accepted it
  as wl-xcon wrote it: `by_kind`, `by_name`, `by_account`, `by_issuer`, `by_token_id`.
- **Problems go into `core.RunRecordProblem`**, which already reaches wl.works as
  `rig_record_problem` flags; its description widens from "wl-xcon's run record" to "wl-xcon's
  record". At most 20 per session, as now.
- A value longer than its column is cut to it, as `core.RunRecord`'s are.

## 4. The session listing

`GET /sessions` and `docs/schemas/session_listing.json`. The schema refuses unknown fields
(`additionalProperties: false` at every level), so this is an amendment for wl.works to take,
written into `docs/pending-wl-works-amendments.md` as earlier changes to the listing were.

- **Each session gains:**
  - `session_kind`: one of the three, or `null`;
  - `calibration`: `{"id", "standard", "measured_on"}`, or `null`. The colour model in `cones` is
    not listed;
  - `accepted_warnings`: each `code`, `detail`, `accepted_in`, `how`, `run_number`, `at_local`, and
    `by` as `{"kind", "name", "account"}` (`account` null for a name typed at the rig) or `null`.
    Never the issuer or the token id.
- **Each run gains `calibration`**: its id, or `null`.
- **Every listed session's entry changes once** when this ships, so wl.works sees each session
  listed once more (the listing re-lists an entry whose digest changes).

## 5. The NWB file and its description

**The file:**
- **`/intervals/runs` gains `calibration`**, the run's calibration id, `''` where it has none.
- **A new processing module, `rig_record`**: what wl-xcon's record says the session was. Written
  only for a session with a `core.SessionRecord` row.
  - `session`, one row: `session_kind`, `calibration_id`, `calibration_standard`,
    `calibration_measured_on` and `calibration_cones` (JSON text), `''` where not recorded;
  - `accepted_warnings`: `code`, `detail`, `accepted_in`, `how`, `run_number` (-1 for one accepted
    at the open) and `accepted_at_local`. No person (§0, decision 3).

**The description** (`contracts/nwb_description.py`, exported to `docs/schemas/nwb_description.json`
and checked in CI) stays at `schema_version` 3: its rule lets a later piece add fields within a
version, never rename or remove one, and a reader ignores fields it does not know. It gains
`session_kind`, `calibration` (`id`, `standard`, `measured_on`), `accepted_warnings` (each `code`
and `run_number`), and each run's `calibration`. The amendment of §4 tells wl.works they exist, so a
dataset can be selected by kind.

---

## 6. What changes, and for whom

- **wl.works:** the listing's amendment (§4) and the description's new fields (§5).
- **wl-xcon:** nothing. Its ask is answered in `HANDOVER-wl-expcontroller.md` once this is merged.
- **A database declared before this piece:** gains three tables on activation. Sessions already
  read keep no record of their kind until their events are read again.
- **The daily report:** unchanged (§0, decision 1).

---

## 7. Testing

Each written first and seen to fail:
- **The reader:** every field read; a `config.json` from before build B gives no kind, and no
  problem; another animal's record is read as nothing; an unreadable line, a warning row without a
  code, and a kind outside the three each become one problem; a warning's run maps to the measured
  run's number through `run_in_session`, and through `run` + 1 for a start row without it.
- **The synthetic session generator** writes `config.json`'s fields and `warnings.jsonl` as
  wl-xcon's code does (§1).
- **A real daemon pass** on such a session fills the three tables and the problems.
- **The listing** carries the new fields, validates against its schema, and never carries an
  issuer or a token id.
- **An NWB build** writes `rig_record` and the runs' `calibration`, read back; the description
  carries the new fields and validates; no person's name from a warning is in the file.
- **Mutation checks** the plan names.
- **The full suite once,** on both interpreters, at the end.

---

## 8. What a plan must verify rather than assume

1. **How the synthetic generator writes `xcon/`** (`synth/peripherals.py`), and every test that
   counts its files or reads `config.json`.
2. **That the events stage's re-read of a session** (a `RunRecord` already present) behaves for the
   new tables as for the old: `skip_duplicates`, or a replace.
3. **Where `GET /sessions` builds an entry** (`listing/entry.py`) and how its digest is taken, so
   the new fields enter it.
4. **Where the NWB description is built** (`nwb/describe.py`) and every test that pins its exported
   schema.
5. **The `<blob>` codec's behaviour on a nested dict with `null`s,** read back unchanged.

---

## 9. Records this changes

- **`HANDOVER-wl-expcontroller.md`**, item 5: answered, at the merge.
- **`docs/pending-wl-works-amendments.md`**: the listing's and the description's new fields (§4, §5).
- **`docs/pending-wl-xcon-amendments.md`**: wl-xcon's PI answered the raw-video ask on 2026-10-12,
  "Yes, it will record", its folder not yet named (wl-xcon's `docs/pending-wl-preproc-amendments.md`,
  section "ANSWERED 2026-10-11", read at `b2c2850`).
- **`docs/CHECKPOINT.md` and `wl.yaml`,** at the merge, as every piece's are.

---

## Amendments, 2026-10-14, made while proving the plan

The plan (`plans/2026-10-14-session-kind-and-warnings.md`) was proven in code before it was
written. These settle what the sections above left open; §0 stands.

1. **A file holds the warnings of the session's open and of its own runs.** §5 did not say which of
   a session's warnings a file of some of its runs holds. One accepted for a run the file does not
   hold is about that run, and stays out (`nwb/gather.py::_rig_record`).
2. **Every problem names its file.** The run record's problems begin `runs.jsonl line N:`, as the
   new ones begin `config.json:` and `warnings.jsonl line N:`, and the listing's `rig_record_problem`
   message begins "the rig's record:", not "the rig's run record:". wl.works sees the wording change;
   its amendment says so.
3. **§8 answered:**
   - **1:** the generator writes `xcon/` in `synth/peripherals.py::write_rig_runs`, for a recipe with
     runs only. A recipe field, `rig_record` (off by default, refused without runs), adds the kind,
     the calibration and the warnings, so every existing profile's files are byte-identical.
   - **2:** the stage's inserts take `skip_duplicates`, as `core.RunRecord`'s do: a session read
     again adds nothing (tested).
   - **3:** `listing/entry.py::build_entry` builds the entry, and its digest is of the whole entry,
     so the new fields enter it; `gather_all` reads each new table once for every session.
   - **4:** `nwb/describe.py::describe`. CI's "Exported schemas are current" step pins
     `docs/schemas`; both `session_listing.json` and `nwb_description.json` are regenerated.
   - **5:** `<blob>` reads a nested record with nulls back unchanged (tested), and
     `tests/schema/test_guardrails.py` pins the new blob attribute, as it pins every one.
4. **A warning's run that will not fit its column** (below 0, or above 65535) is stored null, as a
   value too long is cut: either would otherwise fail the session's whole event stage on every pass.
5. **The description's new fields default** to null and empty, so a description built before them
   still reads as version 3.

## Amendments, 2026-10-14, from the final review

6. **§9 left out `docs/ops/lab-host-protocol.md`,** the wire contract wl.works builds against and keeps
   a copy of, which every earlier change to the listing or the description updated. It now names the
   listing's and the description's new fields, and a test holds it to the listing's models
   (`tests/responder/test_protocol_document.py`).
7. **A description recorded before §5's fields compares as the model reads it now.** Re-read, it gains
   them defaulted, so compared raw it differed from itself, and a correction marker whose swap never
   happened showed wl.works a change for nothing (`nwb/correct.py::as_described_now`).

## Amendment 8, 2026-10-18, the final review's deferred minors

8. **What the minors settled** (the requester's choice, 2026-10-18):
   - **A record of another animal keeps nothing but its problem:** no `core.SessionRecord` row, so its
     file has no `rig_record` module, as §3 says.
   - **A warning joins a run only through the run's start row,** as §2 says. A run whose start line was
     lost keeps its stop reason by `run` + 1, as `core.RunRecord` maps it, but that guess joins no
     warning: wl-xcon's numbers part from it once a session resumes.
   - **A non-finite `at` is read as none,** and **a warning's run that will not fit its column** is
     stored with none and flagged (`rig_record_problem`). The flag is the only sign of it: the listing
     lists the warning with no run number, and the file holds it as accepted at the session's open. Either would otherwise fail the session's event stage on every pass, as
     amendment 4 says of a run.
   - **The listing does not read the colour model** it does not list.
