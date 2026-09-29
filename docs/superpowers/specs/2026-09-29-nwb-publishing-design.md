# NWB publishing: described files on the NAS, where wl.works can find and move them

**Design spec, 2026-09-29.** Piece 2a of Phase 3's NWB export (parent spec
`2026-08-12-wl-preproc-design.md` §8). Piece 1, the builder
(`2026-09-28-nwb-builder-design.md`, merged `a8e1642`), writes one activation's
file to a scratch `nwb_root` and records it in `nwb.NwbFile`. This piece gets
those files off scratch, described, onto the NAS, and findable and movable by
wl.works. Piece 2b, the canonical lifecycle, gets its own spec.

**Why now.** A built file that stays in scratch is useful to nobody, and
`archive/reclaim.py::canonical_nwb_present` is still a hard-coded `False`, so
every scratch reclamation needs a recorded force.

**The requester's decisions, 2026-09-29:**
- **Piece 2 splits in two**: publishing and reporting (this spec) first, the
  canonical lifecycle (2b) second.
- **Three storage sets on one NAS**, which is not yet bought: compressed raw and
  the long-term ("inactive") NWB copies on a large, slow HDD share, and the
  "active" NWBs on a fast NVMe share.
- **A file is active when it belongs to a wl.works dataset someone has marked
  active.**
- **Datasets are defined in wl.works** by task(s) and the specific conditions
  within them, by the experimenter's notes on a block, and by what was actually
  recorded, not what was planned. A dataset builder in wl.works assigns files to
  datasets and knows to move a file when its dataset is marked active.
- **So wl-preproc's files must describe themselves** well enough for that
  builder. The description covers:
  - tasks and conditions;
  - the brain areas recorded, probe types and data types;
  - later, processing results such as the number of single units and whether
    any narrow-waveform units exist.
- **Conditions are named by their stimulus settings**, not by task-specific
  numbers.
- **Each file is described at build time,** and the description is published
  beside it (option 1 of three presented).

---

## 1. What it is, and what it is not

**It is:**
- a description of every NWB file, made by the builder;
- a daemon stage that publishes `written` files from scratch to the NAS;
- a stage that moves files between the fast and slow shares when wl.works asks;
- two responder endpoints, one to list files and one to set the active set;
- a switch of the per-dataset checksums from blake3 to sha256;
- the real `canonical_nwb_present`;
- the matching changes wl.works and wl-xcon must make, drafted as pending
  amendments.

**It is not:**
- **The canonical lifecycle (2b):** the automatic 12-hour canonical activation,
  re-firing, superseding, and rebuilding a file when its inputs change.
- **Ephys content (piece 3):** units, LFP, MUA, the electrode table. The
  description has their slots, empty until then.
- **wl.works' dataset builder itself** (its Plan 24): wl.works builds it, against
  this spec's description and endpoints.
- **The NAS purchase.** This design needs only two mounted paths; §12 records
  the hardware discussion as input, not requirement.

## 2. The file description

**One JSON document per NWB file, made by the builder from the same gathered
data as the file** (option 1). It is:
- stored in `nwb.NwbFile.description`;
- published beside the file as `<identifier>.json`;
- returned by `GET /nwb` (§6).

wl.works' Plan 20 asks for exactly this so that wl.works "never opens the NWB
to decide anything -- it reads the sidecar" (`wl-works/docs/superpowers/specs/
2026-08-04-plan-20-neural-analysis-design.md` lines 476-479).

**Versioned and open.** It carries `schema_version: 1`. Later pieces add groups
and fields, never rename or remove one within a version. A reader ignores
fields it does not know. Its JSON Schema is exported to `docs/schemas` and
checked in CI, as `job_request.json` is.

**Groups, in version 1:**
- `identity`:
  - the NWB `identifier`;
  - `subject`, `session_id`, `session_datetime` and `rig` (the manifest's);
  - `montage_id`, `activation_id`, `role` and `supersedes`;
  - `built_at`, and the pipeline's version and commit;
  - `status` (`written` or `invalid`) and `n_critical`.
- `subject`: `species`, `sex`, `date_of_birth`, and the age in days at the
  session. Each is null where unknown.
- `data_types`: which kinds of data the file holds.
  - Version 1 fills `eye` (gaze and pupil, per eye), `eye_events` (the detectors
    present) and `behaviour` (trial and event counts).
  - `ephys`, `photodiode`, `video` and `stimulation` are present and null until
    their pieces fill them.
- `probes`: an empty list in version 1. Piece 3 gives each probe its type (part
  number), serial, insertion, trajectory, target area(s) and a per-channel area
  summary.
- `blocks`, one entry per block in the file:
  - `block_id`, `works_block_id`, and the task (its code and name);
  - the asserted and measured start and stop;
  - trial counts, total and by outcome;
  - `coverage` per acquisition system;
  - `conditions`: each condition that actually ran in the block, with:
    - `name`, and `code` (the stream's `CONDITION` number, or null);
    - `settings`: the stimulus settings that were constant across that
      condition's trials in this block;
    - `varying`: each setting that varied within the condition, with its range
      (numbers) or its distinct values (anything else, up to 20);
    - trial counts, total and by outcome.
- `quality`: `timing_tier`, the clock's `reference_source`, and each eye's
  fraction of usable samples.
- `processing`: an empty object in version 1. Piece 3 adds the processing
  summary: for example, the number of single units and whether any
  narrow-waveform units exist.
- `checksums`: `algorithm: "sha256"` and one entry per dataset (§7).

*Amended while planning, 2026-09-29 (what proving the code established):*
- *`identity` names the superseded activation `supersedes_activation_id`,
  not `supersedes`. `tests/schema/test_guardrails.py` forbids any
  `"supersedes":` key in the source, the guard that nothing writes
  `Activation.supersedes`, and a clearer name beats weakening it.*
- *`identity.pipeline` is the name and the commit only. The package's version
  is a constant that has never moved, and reading it needs `importlib`, which
  `tests/test_cli_guardrails.py` bans outright.*
- *Why a trial's condition or settings are unknown is one file-level `notes`
  list, not repeated per block: the rig's record is joined for the whole
  file.*

**Not in the description: the file's location.** It changes when the file
moves between shares; `GET /nwb` reports it (§6). **Not ours: the
experimenter's good/bad notes and the experiment links.** wl.works holds them,
and its builder joins them itself.

### 2.1 Conditions come from the rig's own trial record

**What actually ran, by stimulus settings.** wl-xcon writes one line per trial
to `<session>/xcon/trials.jsonl` (`wl_xcon/record.py::Recorder.trial`). Each
line holds:
- `index`, `subject`, `outcome`;
- `block` (the scheduler block's name) and `condition` (the condition's name);
- `params`: "the whole resolved parameter set, per trial".

This follows wl-xcon's own allocation rule: "Event codes carry identity and
timing. The session record carries content." (`wl-expcontroller/docs/
superpowers/specs/2026-08-31-S2-event-vocabulary-design.md`).

- **Joined to our trials by trial number.** The stream's `TRIAL_NUMBER` is
  matched against the record's `index`.
- **Settings are split per condition within a block.** Those constant across the
  condition's trials are its `settings`; the rest are its `varying`.
- **No guessing.** Where the record is missing, a line is malformed, or the
  record and the stream disagree on trial numbers, those trials get no settings,
  and the block's description says why. The build is not refused: the rest of
  the file is as good as ever.
- **Without the record** (older or synthetic sessions), `conditions` lists the
  stream's `CONDITION` numbers with `settings: null`.

*Amended during execution, 2026-09-29 (wl-xcon's reply, its backlog XC-155 at
`4a7d05f`, and its slice b3a-1 plan, decision 4):* *since b3a-1 a session holds
several runs. Each line of `trials.jsonl` names its `run`, each run counts its
trials from 0, and the `TRIAL_NUMBER` wl-xcon will emit is unique within the
session. A per-run `index` is then not the join key even where it is unique:
the stream's trial 50 can be the second run's tenth trial while the only line
with index 50 is that run's fifty-first. So a record whose lines name a run is
not joined at all, and the file's `notes` say why. It is joined again once
wl-preproc reads the key XC-155 records.*

### 2.2 The file itself carries the same information

**The file stands on its own** (parent spec §8.1: self-contained over its
blocks):
- **`/intervals/trials` gains `condition`**, the condition's name, or its number
  as text where no rig record exists. It also gains one `setting_<name>` column
  for each setting that varies anywhere in the session: numbers as `float`
  (NaN where absent), anything else as JSON text.
- **`processing/behavior/conditions`** (`DynamicTable`) has one row per
  condition: `name`, `code`, and its constant `settings` as JSON text.
- The raw per-trial record stays in the session directory, where the rig writes
  it.

*Amended while planning, 2026-09-29: the conditions table's name column is
`condition`, not `name`. A `DynamicTable`'s own `name` attribute shadows a
column called that; measured, pandas returned the table's name for every
row.*

**This reverses a ruling in piece 1** (its §3, "the trials table has no
`condition` column"). That ruling held while a condition lived only on its
`CONDITION` event. The rig's record ties each trial to its condition by trial
number, which is identity, not new meaning. The requester's datasets select by
condition. Piece 1's spec is amended to point here.

## 3. Where files live

**Two NAS locations, given to the daemon,** each as the local mount point of a
share plus that share's name. The daemon's existing `--host` names the NAS, as
it does for the archive:
- `--nwb-slow-root PATH --nwb-slow-share NAME`: the long-term ("inactive") set,
  on the slow share;
- `--nwb-fast-root PATH --nwb-fast-share NAME`: the active set, on the fast
  share.

**The same layout on both:** `<root>/<subject>/<session_id>/<identifier>.nwb`,
with `<identifier>.json` beside it. The recorded path is relative to the
share, as wl.works' location triple requires (`wl-works/docs/superpowers/specs/
2026-08-04-plan-23-dataset-viewer-design.md` §10.1). The roots are
configuration: the lab may give NWBs their own top-level folder on the slow
share, apart from the raw archive's `<subject>/<session>` folders.

*Amended while planning, 2026-09-29: each root is the share's mount point, and
every file lives under a fixed top-level `nwb/` folder on it,
`<mount>/nwb/<subject>/<session_id>/<identifier>.nwb`. That keeps NWBs apart
from the raw archive, and makes the recorded share-relative path
computable from the mount alone.*

**Opt-in, like the archive and the builder.** Without the slow share, publishing
does not run and reports `None`. Without the fast share, placement does not run.

## 4. Publishing

**A daemon stage after the builder.** Its key source is every `NwbFile` with
`status = 'written'` that has no placement yet, skipping freed sessions. For
each, in order:
1. **Choose the share:** fast if the activation is in the current active set
   (§5), otherwise slow.
2. **Copy the NWB** to `<path>.partial` on that share, flushed to disk.
3. **Verify it:** re-hash every dataset (sha256) and compare with the build's
   recorded checksums. On a mismatch, delete the partial copy and report it; the
   next pass retries.
4. **Rename it** to its final name.
5. **Write the description last,** also via `.partial` and a rename. Its
   presence is the "complete" signal. wl.works' Plan 20 asks for "a completion
   sentinel written last, or a write-to-temp-then-rename" (lines 452-453); this
   is both.
6. **Record the placement** (§9) in one transaction.
7. **Delete the scratch copy.**

**Never published:** `invalid` and `refused` files, which stay as piece 1 left
them. **Failures:**
- An unreachable or full share fails that file, is reported per file, and retries
  next pass, like the archive stage.
- A half-copied file never carries its final name, and no placement is recorded
  until verification passes.
- *Amended while planning, 2026-09-29: **publishing never writes over a file
  no placement records.** A row deleted without its published file and then
  rebuilt would otherwise replace that file, and the lab's annotations exist
  only inside it (parent spec §8.3: "regeneration supersedes; it never
  overwrites"). It is refused and reported every pass, for a person to
  resolve.*

## 5. The active set, and placement

**wl.works states the whole active set, every time.** `PUT /nwb/active` (§6)
carries the complete list of activations that belong on the fast share. It is
stored as an append-only row; the latest row is the desired state. Sending the
same list twice changes nothing.

**One live copy per file.** A file is on the fast share or the slow one, never
both. The lab appends annotations into its NWBs (wl.works' Plan 20 §0.1), and
two copies would diverge at the first annotation.

**A placement stage converges on the latest set.** For each published file whose
share differs from the one wanted:
1. copy it, with its description, to the other share, via `.partial`;
2. verify every **written-once** dataset against the build's checksums;
3. rename into place;
4. record the new placement, in one transaction;
5. delete the old copy.

Annotations the lab has appended travel with the file.

**Refusals and limits, each reported:**
- **Changed data.** A written-once dataset whose checksum no longer matches means
  something changed data that must never change. The file is not moved, and the
  report names the dataset, for a person to look at.
- **Headroom.** Moves to the fast share stop at a configured free-space floor
  (`--nwb-fast-headroom-gb`), instead of filling it.
- **Not yet published.** An activation named in the active set but not yet
  published is kept, and publishes straight to the fast share (§4 step 1). So a
  dataset can be marked active before its files exist.
- **Unknown activations** are kept, and named in `PUT /nwb/active`'s response
  (§6). They may be built later.
- *Amended while planning, 2026-09-29:*
  - ***A move whose old copy could not be deleted** stands, and the next pass
    finishes it. That happens when a reader holds the file, or the share
    refuses the delete. Without this, two copies would outlive the move, and
    a later move back would stop on the rule above.*
  - ***A published file missing from its share** is reported by path each
    pass, and its placement stays as recorded.*

## 6. Talking to wl.works

**Pull-only, unchanged:** wl.works opens every connection (parent spec §11.1).
Both new endpoints take the same bearer token as `/health` and `/jobs`, and are
added to `docs/ops/lab-host-protocol.md`. Their bodies' JSON Schemas are
exported to `docs/schemas` and checked in CI.

**`GET /nwb?since=<cursor>`** lists every file whose record changed after the
cursor: built, refused, published or moved. Each entry holds:
- the activation key, `identifier` and `status`;
- the placement: `tier` (`fast` or `slow`), `host`, `share`, `path` and
  `n_bytes`, or null if unpublished;
- the description (§2).

The response carries the next cursor, an integer that only increases. Omitting
`since` lists everything.

**`PUT /nwb/active`** takes `{"activations": [<activation key>, ...],
"requested_by": "<wl.works user or null>"}`. It returns `202` with the count
accepted and the keys it does not know. Placement happens in the daemon's next
pass, and `GET /nwb` shows the result. It uses the same `422` rules as
`POST /jobs` for a malformed body.

## 7. Checksums become sha256

**Piece 1's per-dataset checksums switch from blake3 to sha256**, with every
other rule unchanged:
- the decoded contents of each dataset, never a group;
- a ragged column and its `_index` name each other;
- `/specifications` and `/file_create_date` are skipped.

wl.works' Plan 24 settles the algorithm: "The *design* is settled -- `sha256`,
computed by wl-preproc riding along with a read it was performing anyway"
(`wl-works/docs/superpowers/specs/2026-08-04-plan-24-dataset-builder-design.md`
lines 1287-1289).

`NwbFile.Dataset.blake3` becomes `sha256`. No real file exists yet, so nothing
migrates. Piece 1's spec §7 is amended to point here.

## 8. Disk cleanup: `canonical_nwb_present` becomes real

**True only when every montage of the session has a canonical activation whose
file is published,** on either share. It is false, with the reason stated, when:
- the session has no montage or no canonical activation;
- a canonical file is `invalid` or `refused`;
- a canonical file is built but not yet published.

It stays `overridable`: a recorded force still clears it, as today.

## 9. Tables

**`nwb.NwbFile`** gains:
- `description` (`<blob>`, null for `refused`);
- in its `Dataset` part, `sha256` in place of `blake3`.

**`nwb.NwbPlacement`** (`dj.Manual`, append-only). One row per publish or move:
- `-> NwbFile`, `placement_seq` (the global change cursor, only increasing);
- `tier` (`enum('slow','fast')`), `host`, `share`, `path`, `n_bytes`,
  `placed_at`.

The latest row per activation is its current location, and the rows before it
are its history.

**`nwb.ActiveSet`** (`dj.Manual`, append-only). One row per `PUT /nwb/active`:
- `set_seq`, `received_at`, `requested_by`, and `activations` (`<blob>`).

The latest row is the desired state.

The cursor `GET /nwb` uses covers `NwbFile` and `NwbPlacement` changes. How it
is kept is the plan's to settle (§11.3).

*Settled while planning, 2026-09-29:*
- *The cursor is its own table, `NwbChange`: one row per build, publish or
  move, keyed on `change_seq`, an `auto_increment`. `NwbPlacement` is keyed on
  the change that made it.*
- *`auto_increment` and `LAST_INSERT_ID()` were measured working under
  DataJoint 2.3.*

## 10. What wl.works and wl-xcon must do

Drafted as OPEN entries in `docs/pending-wl-works-amendments.md`, and a new
`docs/pending-wl-xcon-amendments.md` if that repository keeps no such file for
us.

**wl.works:**
- **Its dataset builder (Plan 24) filters on our descriptions.** It needs
  predicates on:
  - the task, and a condition's stimulus settings;
  - data types;
  - probes and areas;
  - later, the processing summary.

  Plan 24's predicates today "live on **blocks**" (its §1.1). The description's
  per-block entries are shaped to join onto them.
- **"Active" is a named record by a person**, not a status column. Plan 24:
  "Status is derived, never stored" (line 60).
- **wl.works computes the activations its active datasets match** and sends them
  with `PUT /nwb/active` when that set changes.
- **It polls `GET /nwb`** to fill `analysis_activation`'s location triple and to
  read each file's checksums, instead of opening files.
- **Its planner's "planned experiments"** stay what they are: intent. Datasets
  select on what was recorded.

**wl-xcon**, only if §11 item 1 finds it does not already:
- emit `TRIAL_NUMBER` equal to its trial record's `index`;
- emit a `CONDITION` number, and record that number beside the condition's name
  in `trials.jsonl`.

*Amended during execution, 2026-09-29:* *§11 item 1 found neither is emitted.
wl-xcon filed it as XC-155, which also changes the first ask: the number is
unique within the session, not the per-run `index` (see §2.1's amendment), and
is recorded in `trials.jsonl` as the join key.*

## 11. What a plan must verify rather than assume

1. **Whether wl-xcon emits `TRIAL_NUMBER` and `CONDITION`,** and whether the
   number equals `trials.jsonl`'s `index`. `wl_xcon/encode.py` knows both
   escapes; whether the task daemon emits them is unread.
2. **The shape of `params` values**: scalars, lists or nested objects. This
   decides the `setting_<name>` columns' types and the `varying` summary.
3. **How to keep the change cursor** under DataJoint 2.3: an auto-increment, or
   a sequence table.
4. **Whether a rename on the NAS's share type (SMB or NFS) is atomic**, so the
   `.partial` rule holds there, as it does locally.
5. **How long sha256 takes** over a large file, measured on the synthetic
   harness scaled up. This is wl.works' own open question (Plan 24's item 1).

## 12. The hardware discussion, 2026-09-29 (input for the purchase, not a requirement)

**From the parent spec's own numbers, at two sessions a week:**
- **Compressed raw:** ~100 GB a session, ~10 TB a year.
- **Long-term NWBs:** up to ~25 GB a file, one to three a session with
  regenerations, so ~2.5-8 TB a year.
- **The active set:** a few months of sessions plus pinned datasets, ~1-3 TB.

**One sketch:**
- an HDD pool with double parity, sized for about five years (~80-100 TB
  usable), for raw and long-term NWBs;
- a mirrored NVMe pool (e.g. 2 x 8 TB) for the active set;
- snapshots on both, because annotations exist only inside the NWBs;
- at least 10 GbE to the compute machine.

## 13. Testing

- **The synthetic session generator writes a realistic `xcon/trials.jsonl`**,
  with conditions, a setting that varies within one condition, and a
  mismatched line in a variant (fix the fixture, not the spec).
- **The description:** its groups, conditions and settings on the synthetic
  session, each "no guessing" case, and its JSON Schema.
- **The NWB additions:** the trials' `condition` and `setting_` columns, and the
  `conditions` table.
- **End to end,** on temporary "slow" and "fast" folders through the daemon:
  1. build, then publish;
  2. `PUT /nwb/active`, then placement moves the file;
  3. `GET /nwb` with and without a cursor;
  4. sha256 verified at each step.
- **Each refusal and failure:**
  - a copy that fails verification;
  - changed written-once data;
  - the headroom floor;
  - an unreachable share;
  - an unpublished or unknown activation in the active set.
- **`canonical_nwb_present`,** true and false in each case of §8.
- **The endpoints over real HTTP,** as `tests/responder/test_http.py` does
  today, including authentication and `422`.
