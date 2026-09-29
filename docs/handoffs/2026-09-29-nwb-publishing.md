# NWB publishing: described files on the NAS, where wl.works can find and move them

Branch `spec/nwb-publishing`, forked from `main` at `85ea88d`. Spec
`docs/superpowers/specs/2026-09-29-nwb-publishing-design.md` (`8fe883a`,
amended in `73c3459` and during execution in `a2e2cf1` and this commit); plan
`docs/superpowers/plans/2026-09-29-nwb-publishing.md` (`73c3459`). Piece 2a of
Phase 3's NWB export. The requester chose publishing as next on 2026-09-29,
split it into 2a (this) and 2b (the canonical lifecycle), and chose inline
execution with one final review.

Every line of the plan was proven in a scratch worktree before the plan was
written, and applied here from it. One deviation, made on news from wl-xcon
during execution, is §6's execution ruling.

---

## 1. What was built

**A description beside every file.** The builder now describes each file it
writes: `nwb/describe.py::describe` returns a JSON document held to
`contracts/nwb_description.py::NwbDescription` (`schema_version` 1, strict,
exported to `docs/schemas/nwb_description.json`). It is made from the same
gathered data as the file, and covers:

- identity (subject, session, montage, activation, role, the superseded
  activation, and the pipeline's name and commit);
- the subject, with the age in days;
- data types, probes (empty until piece 3), and each block's task, intervals,
  trials by outcome, coverage and conditions;
- quality (timing tier, reference source, the eye's usable fraction);
- `notes`, saying why anything is unknown;
- the checksums, now `sha256` (Task 1).

It is stored in `nwb.NwbFile.description`, and published as
`<identifier>.json` beside `<identifier>.nwb`. A refused build has none.

**Conditions from the rig's own record.** `events/rigtrials.py` reads
`<session>/xcon/trials.jsonl`: wl-xcon's one line per trial, with `index`,
`subject`, `outcome`, `block`, `condition` and `params`.
`nwb/conditions.py` joins it to our trials by the stream's `TRIAL_NUMBER`
against `index`, and only where the record names a trial exactly once. It
groups each block's trials by condition, and splits each condition's
settings into those constant across its trials (`settings`) and the rest
(`varying`). A setting whose type differs between trials is written as JSON
text. **A record whose lines name a run is not joined at all** (§6). In the
file:

- the trials table gains a `condition` column and a `setting_<name>` column
  for each setting that varied;
- `processing/behavior/conditions` holds one row per condition.

**Publishing** (`nwb/publish.py::run_publish`, a daemon stage). Every
`written` file that has no placement is copied to
`<slow mount>/nwb/<subject>/<session_id>/<identifier>.nwb`, or straight to the
fast share if it is in the active set and the fast share has room. The copy:

1. is written as `.partial`;
2. is re-hashed against the recorded checksums;
3. is renamed.

The description is then written beside it, the placement recorded, and the
scratch copy deleted. Nothing is written over a file no placement records
(`PublishConflict`). A freed session is skipped.

**Placement** (`run_placement`, the next stage). It keeps one live copy of
each file, on the share the latest active set wants:

- A move re-verifies the source against the written-once checksums first. A
  changed dataset stops it (`ChangedData`).
- A move to the fast share stops at its headroom.
- The lab's annotations move with the file.
- An old copy that could not be deleted is removed on the next pass.
- A published file deleted by hand is reported by path, not moved.

**Tables.**
- `nwb.NwbChange` is an append-only record of `built`, `published` and
  `moved`, with an `auto_increment` sequence that is the listing's cursor.
- `nwb.NwbPlacement`, keyed on the change, holds the tier, host, share, path
  and size.
- `nwb.ActiveSet` holds each active set as received.

**Two endpoints** (`responder/nwb.py`, behind the token as before):

- `GET /nwb?since=<cursor>` returns every activation whose file changed after
  the cursor: its status, reason, current placement and description, plus the
  new cursor.
- `PUT /nwb/active` takes the whole active set and returns 202
  `{accepted, unknown}`. The placement stage acts on it next pass.

Both are in `docs/ops/lab-host-protocol.md` and in
`docs/schemas/active_set_request.json` and `nwb_listing.json`.

**`canonical_nwb_present` is real** (`archive/reclaim.py`). It is true once
every montage of the session has a canonical activation whose file is
`written` and has a placement. Until now it was a hard-coded `False`, so
every reclamation needed a recorded force.

## 2. How to turn it on

```
wlpp daemon --host <nas> --nwb-root <scratch> \
    --nwb-slow-root <slow mount> --nwb-slow-share <slow share name> \
    [--nwb-fast-root <fast mount> --nwb-fast-share <fast share name> \
     --nwb-fast-headroom-gb <GB>]
```

- Each share is named by where it is mounted on this host and by its name on
  the NAS. `--host` names the NAS.
- Files go under a fixed `nwb/` folder on each mount. The recorded path is
  relative to the share.
- Without the slow share, publishing is skipped, and the report's
  `nwb_published` is null. Without both shares, placement is skipped, and
  `nwb_moved` is null. The fast share needs the slow one.
- **Every file is `invalid` until wl.works sends the subject's date of
  birth** (piece 1). `invalid` files are never published. So on real data
  nothing is published until that OPEN entry is done.

## 3. What wl.works and wl-xcon must do

- **wl.works**, a new OPEN entry in `docs/pending-wl-works-amendments.md`:
  - its dataset builder selects on our descriptions;
  - "active" is a person's named record;
  - it sends the activations its active datasets match with
    `PUT /nwb/active`;
  - it polls `GET /nwb` for location, checksums and descriptions instead of
    opening files.
- **wl-xcon**, in a new `docs/pending-wl-xcon-amendments.md`:
  - emit `TRIAL_NUMBER`, unique within the session and recorded on the
    trial's line in `trials.jsonl`;
  - emit `CONDITION`, recording its number beside the condition's name.

  It was sent to the wl-xcon session on 2026-09-29, confirmed there, and
  filed as its **XC-155**. This repository needs the name of XC-155's field
  back.

## 4. The January-critical finding

**A real rig session gives no trials today.** wl-xcon emits no
`TRIAL_NUMBER`: nothing calls its `encode.words_for`. That was read at its
`88e69ac`, re-read at `d1e2ba1`, and confirmed by wl-xcon. This repository
identifies a trial by `TRIAL_NUMBER` alone, so without it there are no
trials, no conditions and no per-block trial counts in any real file. Every
test here runs on the synthetic session, which emits both codes. XC-155 must
land before the lab's first real sessions.

## 5. Still not done

- **Piece 2b, the canonical lifecycle:**
  - the automatic 12-hour canonical activation;
  - re-firing;
  - superseding;
  - rebuilding `invalid` rows once the date of birth arrives, and after
    upstream recomputes.
- **Reading XC-155's join field**, once wl-xcon names it, so that multi-run
  records join again.
- **Piece 3, ephys**, which fills `probes` and the processing summary.

## 6. The rulings

**Made while planning** (the plan's "Rulings made while planning"):

1. The superseded activation is named `supersedes_activation_id`. A
   guardrail forbids any `"supersedes":` key.
2. `identity.pipeline` is the name and commit only, because reading the
   version needs `importlib`, which is banned. *Cost if wrong:* an installed
   deployment that is not a checkout records no commit.
3. Why a trial's condition or settings are unknown is one file-level `notes`
   list.
4. The conditions table's name column is `condition`. A `DynamicTable`'s own
   `name` shadows it.
5. Files live under a fixed `nwb/` folder on each share.
6. The change cursor is its own table, `NwbChange`, and `NwbPlacement` is
   keyed on it.
7. Publishing never writes over a file no placement records. *Cost if wrong:*
   a person removes the stale file before rebuilding.
8. A move whose old copy could not be deleted is finished on the next pass.
   A missing published file is reported by path.
9. Test rows with no file behind them are removed by the tests that make
   them (the blob guardrail's, and the reclaim test's).
10. A file in the active set publishes straight to the fast share only if
    there is room.
11. wl-xcon emits no `TRIAL_NUMBER` today, so this piece is built and tested
    against the synthetic session. *Cost if wrong:* none to this code.

**Made while executing:**

- **A rig record whose lines name a run is not joined at all.** On
  2026-09-29 wl-xcon replied that since its slice b3a-1:
  - each line of `trials.jsonl` names its `run`, and each run counts its
    trials from 0 (its b3a-1 plan, decisions 2 and 4);
  - the `TRIAL_NUMBER` it will emit is unique within the session (XC-155).

  Joining that number to a per-run `index` can pair a trial with a different
  trial's unique line: stream trial 50 can be run 1's tenth trial, while the
  only line with index 50 is run 1's fifty-first. The plan's per-index
  "exactly once" rule cannot see this. So `read_rig_trials` returns no trials,
  and one problem saying why, when any of the subject's lines carries `run`.
  The test is `test_a_record_that_numbers_trials_within_each_run_is_not_read`
  (RED, then GREEN), with mutation T2g caught. The spec is amended in §2.1 and
  §10, and the wl-xcon entry now asks for the session-unique number.
  *Cost if wrong:* a multi-run session's conditions stay empty until this
  repository reads XC-155's field. They are never wrong.

## 7. The measured counts

| Task | Before the code | After | Mutations |
|---|---|---|---|
| 1 sha256 | 2 failed, 18 passed, 1 skipped | 20 passed, 1 skipped | T1a caught |
| 2 the rig's record | 12 failed, 17 passed | 29 passed; wider set 388 passed | T2a–T2g caught |
| 3 conditions in the file | 4 failed, 33 passed | 37 passed | T3a–T3c caught |
| 4 the description | 7 failed, 73 passed, 1 skipped | 80 passed, 1 skipped | T4a–T4d caught |
| 5 publishing | 10 failed, 83 passed, 1 skipped, 4 errors | 97 passed, 1 skipped | T5a–T5e caught |
| 6 placement | 7 failed, 85 passed, 1 skipped | 92 passed, 1 skipped | T6a–T6f caught |
| 7 the endpoints | 13 failed, 168 passed | 181 passed | T7a–T7e caught |
| 8 `canonical_nwb_present` | 6 failed, 100 passed | 106 passed | T8a–T8b caught |

Task 2 has one more test than the plan: the execution ruling's, which is in
its before and after counts. The three exported schemas are identical to the
proven ones.

**Full suite, once, with every task applied** (BMD and NSLR references set,
`WLPP_OHDPI_REFERENCE` unset, as CI has it): **1887 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11 and
**1886 passed, 33 skipped, 1 xfailed**, 0 failed on 3.13. The plan measured 1886 and 1885 before the execution
ruling's test was added; `main` at `85ea88d` gives 1829 and 1828.
