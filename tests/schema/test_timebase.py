"""The timebase tables, and three declarations that were always derived.

Every change here is free precisely because no row exists anywhere yet — the
same argument parent spec section 5.1.1 makes for the `<blob>` fix. After
January each one is a migration on live foreign-keyed tables.
"""

import datajoint as dj
import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import core, coverage, timebase

    timebase.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    return core, coverage, timebase


def test_derived_tables_are_computed_not_manual(schemas):
    """These are derived quantities with no human author. Declared Manual in
    1c-1 when nothing computed them; Computed now that something does. Free
    while no row exists anywhere, a migration once one does."""
    core, coverage, _timebase = schemas

    assert issubclass(core.Segment, dj.Computed)
    assert issubclass(coverage.BlockCoverage, dj.Computed)
    assert issubclass(coverage.TrialCoverage, dj.Computed)


def test_the_new_timebase_tables_are_computed_too(schemas):
    """Both are fits over recordings. Nothing about either has a human author,
    and declaring them Manual would leave a slot a person could fill by hand
    with a number nothing derived."""
    _core, _coverage, timebase = schemas

    assert issubclass(timebase.SystemTimebase, dj.Computed)
    assert issubclass(timebase.TimingProvenance, dj.Computed)


def test_rejected_segment_stays_manual_because_its_key_cannot_be_computed(schemas):
    """RejectedSegment is keyed on file_path precisely because a file yielding
    zero barcodes has no segment_barcode to key on. It records a fact about a
    file, not a computation over one."""
    core, _coverage, _timebase = schemas

    assert issubclass(core.RejectedSegment, dj.Manual)


def test_segment_carries_what_makes_the_transform_reversible(schemas):
    """Spec section 4.5 requires fit parameters, residuals and native stream
    timestamps be retained so every transform is reversible and auditable.
    Storing them on the row makes that a property of the data rather than a
    promise in a document."""
    core, _coverage, _timebase = schemas

    attrs = set(core.Segment.heading.attributes)
    assert {"offset_s", "residual_us", "n_barcodes", "first_sample", "file_path"} <= attrs


def test_the_timebase_records_which_clock_it_believed(schemas):
    """A camera aligned by barcode is precise to one frame period — 2 ms at
    500 Hz (design spec section 3.1) — while one aligned by an external trigger
    is exact. A downstream analysis that cares about 2 ms must be able to tell
    which it got, so the distinction is a stored column rather than something
    inferred from the rate."""
    _core, _coverage, timebase = schemas

    time_source = timebase.SystemTimebase.heading.attributes["time_source"]
    assert "barcode" in time_source.type
    assert "trigger" in time_source.type


def test_the_tier_enum_no_longer_holds_pending(schemas, enum_values):
    """Before 1c-5, tiers A/B/C each needed a code-agreement or trial-count
    term that event decoding alone could supply, so 'pending' was a value the
    column could hold rather than a default that reads like a verdict --
    which this test asserted directly, by checking 'pending' was IN the
    declared type.

    1c-5 Task 9 is what supplies those inputs: `TimingProvenance.make()` now
    calls `events.agreement.resolve_tier` unconditionally, so no code path
    writes 'pending' any more. It is dropped from the enum outright rather
    than kept as a value nothing can reach -- a stored value with no writer
    left is worse than no value at all, per `_TIER_ENUM`'s own comment in
    `schema/timebase.py`. So this test now asserts the opposite of what it
    used to: the enum is EXACTLY {A, B, C, D}, and 'pending' is gone."""
    _core, _coverage, timebase = schemas

    tier = timebase.TimingProvenance.heading.attributes["tier"]
    assert enum_values(tier.type) == {"A", "B", "C", "D"}


def test_no_bare_longblob_in_the_new_schema_module(schemas):
    """The guardrail sweep auto-discovers schema modules, so this is belt and
    braces — but a bare longblob silently stores a numpy array as its truncated
    string repr and nothing raises on insert or fetch."""
    _core, _coverage, timebase = schemas

    assert "longblob" not in timebase.SystemTimebase.definition
    assert "longblob" not in timebase.TimingProvenance.definition


def test_block_does_not_claim_to_decode_its_own_boundaries(schemas):
    """Closed open item 9: block rows are authored by wl.works' session planner
    and wl-preproc NEVER writes them — it cross-validates and quarantines on
    absence. The column comment claimed the opposite mechanism ("boundaries are
    decoded from event codes and cross-validated against those rows") for two
    phases, and 1c-3 predicted the decoder would "find the slot occupied". It
    resolved the other way: `accept()` was right and the comment was wrong.
    """
    core, _coverage, _timebase = schemas

    assert "decoded from event codes" not in core.Block.definition
    assert "wl.works" in core.Block.definition


# --- TimingProvenance.make(). These need a landed, populated session. ---

import datetime  # noqa: E402


@pytest.fixture(scope="module")
def provenance_session(dj_conn, prefix, tmp_path_factory):
    """A landed `drift` session with every populate run, provenance last --
    including `events.populate_session`, which 1c-5 Task 9's tier resolution
    now depends on for `trial_count_agreement` (codes vs task file) and
    `block_agreement` (measured `trial.Block` vs asserted `core.Block`).

    Before Task 9 this fixture never called `events.populate_session` at all,
    because nothing here needed `pipeline.trial.Trial`/`trial.Block` to exist.
    That premise is gone: leaving it out would make every session decode as
    though the codes were never assembled, forcing a spurious
    `trial_count_agreement=False` and quarantining a clean fixture at D for a
    reason that has nothing to do with what each test actually exercises. Per
    "fix the fixture, not the spec" -- the fixture predates the dependency,
    not the design.

    `core.Block` gets one row asserting the DRIFT recipe's own NOMINAL block
    boundary independently -- `start_s=0.0`, `end_s=recipe.duration_s` --
    NOT read back from `pipeline.trial.Block`. Fix round 2 tried the latter
    after tightening `block_agreement_tolerance_s`'s floor made the nominal
    assertion fail, but review (fix round 3) caught that this made
    `block_agreement` compare a value against itself: `trial.Block`'s
    columns are float32 and `core.Block`'s are double, so a float32 value
    written into a double and read back is bit-exact, and the "positive
    path" this fixture exists to exercise could never again detect any
    disagreement. wl.works asserting the nominal boundary and the decoder
    measuring it one code-word slot later (`timeline.py`'s own `_emit`
    ratchets `BLOCK_START`'s escape word one slot past `SESSION_START`) is
    not a fixture defect to route around -- it is exactly the real
    relationship this fixture is supposed to model, and
    `block_agreement_tolerance_s`'s floor is now derived from that same
    one-slot transport quantization (see its own module-level comment), so
    it absorbs the ratchet correctly without the fixture needing to already
    know the measured answer. `test_block_agreement_true_has_teeth_against_a_
    real_perturbation`, below, is what proves this path still has teeth
    after the revert.
    """
    from wl_preproc.schema import core, coverage, events, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    timebase.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    events.activate(prefix=prefix)

    root = tmp_path_factory.mktemp("provenance")
    recipe = RECIPES["drift"]
    generate_session(root, recipe)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 20, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 20, 19, 0),
            "session_dir": str(root / recipe.session_id),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    core.Block.insert1(
        {
            **session_key,
            "block_id": 1,
            "task_type": "rf_map",
            "start_s": 0.0,
            "end_s": recipe.duration_s,
        },
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    events.populate_session(session_key, root / recipe.session_id)
    timebase.TimingProvenance.populate()
    return session_key, recipe


def test_the_tier_resolves_to_a_once_two_full_code_records_genuinely_agree(
    schemas, provenance_session
):
    """1c-5 Task 9 supplies what 1c-4 could only wait for. Before this task,
    this exact fixture's tier read 'pending' (this test's own former name);
    now `TimingProvenance.make()` always calls `resolve_tier`, and this
    session has everything tier A needs: the DRIFT recipe carries both
    `syncbox` (the Pi) and `spikeglx` (the NI, since 1c-5's Task 1 gave the
    synthetic NI its own code lines), so there are two independent full-code
    records: 1.0 -- the NI latches whatever value is on the shared strobe bus
    at that instant, which does not depend on either system's own clock rate,
    so the two decode identically regardless of the NI's planted 18 ppm
    drift. `trial_count_agreement` is `True`: the task file and the decoded
    code stream both derive from the identical planted `GroundTruth.trials`.
    `block_agreement` is `True` too -- but NOT because the two values match
    exactly, which they provably do not. The fixture asserts one `core.Block`
    row at the DRIFT recipe's NOMINAL boundary (`start_s=0.0`), while the
    measured `trial.Block` starts one code-word slot later, at `0.001`: the
    ratchet `provenance_session`'s own docstring describes. It agrees because
    `block_agreement_tolerance_s`'s floor is derived from exactly that one-slot
    transport quantization and absorbs it -- so this positive path exercises
    the tolerance rather than bypassing it. Produces: tier A, by the
    `n_full_code_records >= 2` branch, with none of the D guards tripped.
    """
    _core, _coverage, timebase = schemas
    session_key, _recipe = provenance_session

    row = (timebase.TimingProvenance & session_key).fetch1()

    assert row["tier"] == "A", row
    assert row["pending_inputs"] == ""
    assert row["n_full_code_records"] == 2
    assert row["event_code_agreement"] == pytest.approx(1.0)
    assert row["decode_errors"] == 0
    assert row["trial_count_agreement"]
    assert row["block_agreement"]


def test_tier_d_is_fully_derivable_now(schemas, dj_conn, prefix, tmp_path):
    """Any timing check failed. Tier D needs no code agreement and no trial
    counts, so it is the one verdict this phase can reach on its own — and
    reaching it must not require 1c-5.

    The session here has a system that decoded nothing, which is exactly a
    failed timing check.

    **Also verifies fix round 2's I1 correction.** The first draft of
    `make()` gated ALL evidence-gathering behind `not failed`, so a
    timing-failed session's row stored zeros and NULLs for every
    `TierInputs`-derived column -- byte-identical to a genuinely
    uncorroborated session, and parent spec section 4.7's re-derivability
    was lost exactly where a human is most likely to go looking (a
    quarantined session). Only `bcam` is corrupted here; `syncbox` and
    `spikeglx` are both untouched and fully decodable, so this row should
    show real evidence (`n_full_code_records == 2`) despite `tier == "D"`.
    """
    import yaml

    from wl_preproc.schema import core, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.extract import find_recordings

    ingest.activate(prefix=prefix)
    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    sidecar = find_recordings("bcam", session_dir / "bcam")[0]
    payload = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    payload["digital_line"] = [0] * payload["frame_count"]
    sidecar.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 21, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 21, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    timebase.TimingProvenance.populate()

    row = (timebase.TimingProvenance & session_key).fetch1()
    assert row["tier"] == "D"
    assert row["n_rejected_segments"] >= 1
    # I1: real evidence, not a zeroed-out row, despite the timing failure.
    assert row["n_full_code_records"] == 2, (
        "syncbox and spikeglx are both clean here -- only bcam is corrupted "
        f"-- so this must not read as though nothing was ever decoded: {row}"
    )


def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
    schemas, provenance_session
):
    """Spec section 4.7 requires the tier be derived, not asserted, and
    re-derivable under different thresholds later. That is only true if every
    input is on the row — a stored verdict with the evidence discarded can be
    re-read but never re-judged.

    **Fix round 1** (coordinator review): this test's own opening claim is
    "every input", but until this round it checked only the six columns that
    predate 1c-5 Task 9 and none of the four `TierInputs` fields Task 9 added
    -- `event_code_agreement`, `trial_count_agreement`, `camera_trigger_count`,
    `block_agreement` -- which are exactly the evidence the re-derivability
    clause is about. Extended to check all seven new columns (those four plus
    `n_full_code_records`, `n_strobe_witnesses`, `decode_errors`), and against
    values this test derives independently rather than merely checking the
    keys exist: `camera_trigger_count` against `synth.peripherals.
    camera_frame_count(recipe)` (no `Fault.DROPPED_CAMERA_FRAMES` in this
    recipe, so nothing should be subtracted from it), and the rest against
    what the `drift` recipe's own topology implies -- two full-code records
    (`syncbox` + `spikeglx`) that must agree exactly regardless of the NI's
    planted drift, one valid `rhs` strobe witness, zero decode errors, and a
    genuine (not merely non-`None`) trial-count and block-boundary match, both
    of which this fixture's own `core.Block` insert and populated `Trial` rows
    make true rather than assumed. Verified directly against a live run
    before writing the literals below, per `provenance_session`'s current
    state (`tests/schema/test_timebase.py`'s own module).
    """
    from wl_preproc.synth.peripherals import camera_frame_count

    _core, _coverage, timebase = schemas
    session_key, recipe = provenance_session

    row = (timebase.TimingProvenance & session_key).fetch1()

    assert row["n_barcodes_emitted"] > 0
    assert row["n_systems_aligned"] == len(recipe.systems)
    assert row["n_segments"] == len(recipe.systems)
    assert row["n_rejected_segments"] == 0
    assert row["worst_residual_us"] >= 0.0
    # The largest planted magnitude, recovered through the database.
    assert abs(row["worst_drift_ppm"]) == pytest.approx(
        max(abs(ppm) for _s, ppm in recipe.system_drift_ppm), abs=300.0
    )

    # -- The four TierInputs fields Task 9 added, and the tier's own
    # derived-count fields: present AND holding what this session's real
    # topology implies, not merely non-null.
    assert row["n_full_code_records"] == 2, "syncbox + spikeglx, both present"
    assert row["n_strobe_witnesses"] == 1, "rhs is present and a valid witness"
    assert row["decode_errors"] == 0, "a clean fixture with no injected fault"
    assert row["event_code_agreement"] == pytest.approx(1.0), (
        "the NI latches whatever the shared bus carries, independent of "
        "either clock's own rate, so drift must not affect this"
    )
    assert row["trial_count_agreement"], (
        "the task file and the decoded trial list both derive from the same "
        "planted GroundTruth.trials"
    )
    assert row["camera_trigger_count"] == camera_frame_count(recipe), (
        "no DROPPED_CAMERA_FRAMES fault on this recipe, so nothing should be "
        "subtracted from the full frame count"
    )
    assert row["block_agreement"], (
        "this fixture's own core.Block row asserts the NOMINAL boundary "
        "(start_s=0.0); the measured trial.Block starts one code-word slot "
        "later at 0.001, inside the derived tolerance -- not an exact match"
    )


def test_the_tier_leaves_pending_once_the_three_inputs_exist(dj_conn, prefix):
    """1c-4 recorded `tier='pending'` and named exactly what it waited for:
    event_code_agreement, trial_count_agreement, camera_trigger_count. This
    phase supplies them, so no session may still read 'pending' afterwards --
    and `pending_inputs` must be emptied rather than left describing a wait
    that is over.

    Every row already in the shared database by the time this test runs is in
    scope, not only this file's own fixtures: `tests/conftest.py`'s `dj_conn`/
    `prefix` are session-scoped, one database for the whole suite, so any
    other test file that has populated `TimingProvenance` by now contributes
    rows here too. That is deliberate -- the invariant this test states is
    "no code path writes 'pending' any more", and a check restricted to this
    file's own rows could not tell that from "this file's fixtures happen not
    to trigger the old path".

    (Mid-flight correction: the first draft of this test wrapped the
    `pending_inputs == ""` assertion in `if row["tier"] != "pending"`. Dead
    code -- the assertion just above it already requires `tier` to be one of
    A/B/C/D, so the guard's condition is unconditionally true and reads, to
    anyone skimming it, as though pending rows were deliberately exempted from
    the very check this test exists to enforce. Removed; both assertions now
    run unconditionally for every row.)
    """
    from wl_preproc.schema import timebase

    rows = timebase.TimingProvenance.to_dicts()
    assert rows, "no provenance rows to check"
    for row in rows:
        assert row["tier"] in {"A", "B", "C", "D"}, f"still pending: {row}"
        assert row["pending_inputs"] == "", (
            "pending_inputs must be empty once the tier is decided; it "
            f"still reads {row['pending_inputs']!r}"
        )


def test_block_disagreement_forces_d_even_with_two_agreeing_full_code_records(
    dj_conn, prefix, tmp_path
):
    """Design spec section 5: "A disagreement between `trial.Block` (measured)
    and `core.Block` (asserted) is a tier-D condition, not a silent
    reconciliation" -- proven end-to-end here, not only at
    `resolve_tier`'s own unit level (`tests/events/test_agreement.py`).

    One of the `ci` recipe's two blocks (RF_MAP: 3 trials * 3.0s, then
    RESTING_DARK: 1 trial * 6.0s) genuinely spans `[0.0, 9.0)` -- fix round 2
    correction: this docstring previously called it the recipe's "own single
    block", which is wrong; `core.Block` instead asserts `end_s=999.0`, a
    wl.works row that does not describe this session at all. Every other
    input is
    clean: `syncbox` + `spikeglx` give two agreeing full-code records (tier A
    territory otherwise), and the task file's trial count matches the decoded
    one exactly. Produces: `block_agreement=False`, and a tier of D despite
    satisfying every other tier-A condition -- proving `block_agreement`
    actually gates `TimingProvenance.make()`'s own resolved tier, not merely
    `resolve_tier` in isolation.
    """
    import datetime

    from wl_preproc.schema import core, events, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    ingest.activate(prefix=prefix)
    events.activate(prefix=prefix)
    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 23, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 23, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )
    core.Block.insert1(
        {
            **session_key,
            "block_id": 1,
            "task_type": "rf_map",
            "start_s": 0.0,
            "end_s": 999.0,  # disagrees with the measured trial.Block outright
        },
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    events.populate_session(session_key, session_dir)
    timebase.TimingProvenance.populate()

    row = (timebase.TimingProvenance & session_key).fetch1()
    # `== 0`, not `is False`: a `tinyint(1)` column round-trips through
    # DataJoint/pymysql as a plain Python int, not the `False` singleton, so
    # an `is` comparison here would fail on genuinely correct data.
    assert row["block_agreement"] == 0, row
    assert row["tier"] == "D", row
    assert row["n_full_code_records"] == 2, (
        "this must be a genuine A-territory session apart from the block "
        f"disagreement, or D proves nothing about block_agreement: {row}"
    )


def test_a_session_with_no_corroboration_at_all_resolves_to_d(
    schemas, dj_conn, prefix, tmp_path
):
    """Fix round 1 (coordinator review). `test_tier_is_pending_not_a_passing_
    grade_when_inputs_are_missing` used to guard a principle that outlived
    the word 'pending': missing inputs must never be treated as passing, a
    false claim of validation. Retiring that test (it asserted a state that
    no longer exists) was correct, but nothing at TABLE level replaced its
    principle -- `test_tier_d_is_fully_derivable_now` covers a FAILED timing
    check, and `test_block_disagreement_forces_d_...` covers a genuine block
    disagreement; neither covers a session that passes every timing check
    outright and simply has nothing corroborating it. `tests/events/
    test_agreement.py` guards `resolve_tier` itself at the unit level, but
    that cannot catch `TimingProvenance.make()` handing it a FABRICATED
    input -- exactly the shape of defect Task 7's fix round 1 corrected
    inside `resolve_tier` (a verdict asserting a check that never happened),
    one layer up, in the code that supplies it.

    **What this fixture genuinely produces, built rather than simulated:**
    no named recipe in `synth/recipe.py` reaches "one full-code record, no
    witness, no successful task-file check" honestly -- every recipe with
    `spikeglx` gives a second full-code record (tier A territory), `rhs`
    gives a witness (tier B territory), and every recipe's task file is
    written unconditionally by `session.py` with a trial count that always
    matches the decoded one exactly, since both derive from the same planted
    `GroundTruth.trials` (so leaving it in place would genuinely earn C, not
    demonstrate the gap this test exists to close). So this builds a
    minimal `SessionRecipe` directly -- `systems=("syncbox",)` alone, the
    "behaviour-only training day" topology `test_one_full_code_record_alone_
    is_c` already names in `tests/events/test_agreement.py` -- and then
    deletes the task file `generate_session` wrote, a real, honest
    filesystem state (a task file that never transferred, or was never
    written), not a patch to any function under test.

    Verified directly (this docstring is not a guess): with this recipe and
    the task file removed, `n_systems_aligned == len(systems) == 1` and
    `n_rejected_segments == 0` -- every timing check genuinely passes, so
    this reaches D through `resolve_tier`'s own bare fallthrough, never
    through the pre-existing timing-check fast path `test_tier_d_is_fully_
    derivable_now` covers. `n_full_code_records == 1` (only syncbox: no
    `spikeglx`, so no second record; no `rhs`, so `n_strobe_witnesses == 0`),
    and `trial_count_agreement`/`event_code_agreement`/`camera_trigger_count`/
    `block_agreement` are all `None` -- nothing corroborated this session,
    which is the row's own evidence for why D is correct here, not merely
    that it is D.
    """
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.schema import core, events, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe
    from wl_preproc.synth.session import generate_session

    ingest.activate(prefix=prefix)
    events.activate(prefix=prefix)

    recipe = SessionRecipe(
        session_id="2027-03-25_01",
        subject="pico",
        rig="rig-a",
        systems=("syncbox",),  # no spikeglx (no 2nd record), no rhs (no witness)
        blocks=(
            BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=2, trial_duration_s=3.0),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=6.0),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=42,
    )
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    # The one deliberate absence: a real file, written by the generator, then
    # removed -- not a patch to `SyntheticTaskFileReader` or `resolve_tier`.
    (session_dir / "syncbox" / "task.json").unlink()

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 25, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 25, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    events.populate_session(session_key, session_dir)
    timebase.TimingProvenance.populate()

    row = (timebase.TimingProvenance & session_key).fetch1()

    # Every timing check genuinely passed -- this is NOT the pre-existing
    # failed-timing route.
    assert row["n_systems_aligned"] == len(recipe.systems) == 1, row
    assert row["n_rejected_segments"] == 0, row

    # Nothing corroborated this session -- the row's own evidence for D.
    assert row["n_full_code_records"] == 1, row
    assert row["n_strobe_witnesses"] == 0, row
    assert row["decode_errors"] == 0, row
    assert row["event_code_agreement"] is None, row
    assert row["trial_count_agreement"] is None, row
    assert row["block_agreement"] is None, row

    assert row["tier"] == "D", (
        "one full-code record, no witness, no successful task-file check: "
        f"none of A, B or C is satisfied, so this must be D, not {row['tier']!r}"
    )


def test_block_agreement_true_has_teeth_against_a_real_perturbation(
    schemas, dj_conn, prefix, tmp_path_factory
):
    """Fix round 3 (coordinator review): the positive path -- a `core.Block`
    row that genuinely matches the measured boundary -- has to be able to
    detect a real disagreement, not merely fail to crash. Reverting
    `provenance_session`'s fixture to assert the nominal boundary
    independently (rather than reading `pipeline.trial.Block` back, which
    made the comparison bit-exact and therefore untestable) means that
    session alone no longer proves this; this test does, with its own
    session so it is not entangled with `provenance_session`'s module-scoped
    cache.

    Same `drift` recipe, same nominal assertion `provenance_session` uses
    (`start_s=0.0`, `end_s=recipe.duration_s`) -- except `start_s` is
    perturbed by `+0.1` s (100 ms), two orders of magnitude past
    `block_agreement_tolerance_s`'s derived floor (2 ms at this small a
    magnitude: one code-word slot, doubled for float32-rounding headroom).
    100 ms is not a boundary case -- it is comfortably larger than the
    tolerance in either direction this fixture could plausibly reach, so a
    tier that does not move here means the positive path proves nothing.

    Produces: every other input identical to a genuine tier-A session
    (`syncbox` + `spikeglx` give two agreeing full-code records; `rhs` gives
    a valid witness; the task file's trial count matches the decoded one
    exactly) -- so the ONLY thing standing between this session and tier A
    is the perturbed `start_s`. `block_agreement` must read `False` and the
    tier must move to D.
    """
    from wl_preproc.schema import core, coverage, events, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    timebase.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    events.activate(prefix=prefix)

    root = tmp_path_factory.mktemp("perturbed")
    recipe = RECIPES["drift"]
    generate_session(root, recipe)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 26, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 26, 19, 0),
            "session_dir": str(root / recipe.session_id),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )
    core.Block.insert1(
        {
            **session_key,
            "block_id": 1,
            "task_type": "rf_map",
            "start_s": 0.0 + 0.1,  # perturbed: 100ms past the nominal 0.0
            "end_s": recipe.duration_s,
        },
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    events.populate_session(session_key, root / recipe.session_id)
    timebase.TimingProvenance.populate()

    row = (timebase.TimingProvenance & session_key).fetch1()
    assert row["n_full_code_records"] == 2, (
        f"this must be genuine A-territory apart from the perturbation: {row}"
    )
    # block_agreement is computed independently of `failed` (gated on
    # `syncbox_fitted` alone, per TimingProvenance.make()'s own comment), so
    # this genuinely proves the 100ms perturbation was detected through the
    # full production path.
    assert row["block_agreement"] == 0, row
    # Every system in `RECIPES["drift"]`, ohdpi included, now aligns
    # (`n_systems_aligned == len(recipe.systems)`, checked directly while
    # restoring this assertion), so `failed` is False and `tier` comes from
    # `resolve_tier(inputs)` -- the `block_agreement` branch above, not the
    # `tier = "D" if failed else ...` short-circuit. This is a genuine
    # second, end-to-end check of the same branch
    # tests/events/test_agreement.py's
    # test_block_disagreement_is_D_even_with_two_agreeing_full_code_records
    # and test_block_agreement_true_does_not_block_tier_a exercise directly.
    assert row["tier"] == "D", (
        f"a 100ms start_s perturbation must move the tier off A: {row}"
    )
