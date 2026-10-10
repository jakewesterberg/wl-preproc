"""`DetectionQuality` populates (design spec
`docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md` sections 3,
4 and 7): end to end through `daemon.run_once()` on `test_detect_populate.py`'s
stepped session, and on planted detection rows where a hand-set trace shows
what a rule does. A planted session is deleted after its test, with everything
hanging from it, so no later daemon pass in the suite meets it."""

from __future__ import annotations

import datetime

import pytest

from tests.identities import new_animal
from tests.schema.test_detect_populate import _build_stepped_session
from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs, runs_from_labels

TRACES = ("left", "right", "conjunction")
S, F = Label.SACCADE, Label.FIXATION


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only: `daemon.run_once()` registers the paramsets."""
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def stepped(daemon_module, prefix, tmp_path_factory):
    session = new_animal().session()
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="qualitystep", session_id=session.session_id, subject=session.subject,
        session_datetime=session.session_datetime, seed=1101)
    daemon_module.run_once(prefix=prefix)
    return session_key


def _registered() -> dict[int, str]:
    """Each detector's default `eye_detection` paramset, by index, with its
    name: the paramsets blended, one per detector (spec amendment 5). Other
    modules leave other paramsets registered, one for a detector since
    removed among them."""
    from wl_preproc.schema import detect

    return {idx: name for name, idx in detect.register_default_paramsets().items()}


def _stored_labels(detection: dict):
    from wl_preproc.schema import detect

    key = {name: detection[name] for name in detect.EyeDetection.primary_key}
    runs = (detect.EyeDetection.Run & key).to_dicts(order_by="run_index")
    return labels_from_runs([Run(run["run_start"], run["run_stop"], Label(run["label"])) for run in runs],
                            detection["n_samples"])


def test_every_trace_gets_a_row_per_metric_and_convention(stepped):
    from wl_preproc.eye.detect.consensus import BLENDED_METRICS
    from wl_preproc.schema import consensus

    rows = (consensus.DetectionQuality & stepped).to_dicts()
    assert sorted((row["trace"], row["metric"], row["pso_as"]) for row in rows) == sorted(
        (trace, metric, pso_as) for trace in TRACES for metric in BLENDED_METRICS for pso_as in consensus.PSO_AS_VALUES)
    assert {row["detectors"] for row in rows} == {",".join(str(idx) for idx in sorted(_registered()))}
    assert {row["vocabulary"] for row in rows} == {"saccade,fixation"}


def test_each_value_is_the_blend_of_the_stored_labels(stepped):
    """Every stored value is `blended_agreement` over the stored runs, BMD
    abstaining on Engbert-Kliegl's saccades."""
    from wl_preproc.eye.detect.consensus import BLENDED_METRICS, blended_agreement
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema import consensus, detect

    registered = _registered()
    engbert_kliegl = min(idx for idx, name in registered.items() if name == "engbert_kliegl")
    for row in (consensus.DetectionQuality & stepped).to_dicts():
        detections = (detect.EyeDetection & stepped & {"trace": row["trace"], "paramset_type": "eye_detection",
                                                       "validity_paramset_idx": row["validity_paramset_idx"]}).to_dicts()
        detections = [d for d in detections if d["paramset_idx"] in registered]
        labels = {str(d["paramset_idx"]): _stored_labels(d) for d in detections}
        vocabularies = {str(d["paramset_idx"]): get_detector(registered[d["paramset_idx"]]).vocabulary for d in detections}
        copies = {str(d["paramset_idx"]): str(engbert_kliegl) if registered[d["paramset_idx"]] == "bmd" else None
                  for d in detections}
        result = blended_agreement(labels, vocabularies, copies, row["pso_as"], BLENDED_METRICS[row["metric"]])
        assert row["n_samples_compared"] == result.n_samples_compared
        assert row["value"] == pytest.approx(result.value)
        assert 0.0 < row["value"] < 1.0


@pytest.fixture
def planted(daemon_module):
    """A bare session, deleted with everything hanging from it afterwards.
    Returns its key and the default validity paramset's index. Registers the
    detectors itself: run alone, no daemon pass has registered them, and a
    test planting one detection per registered detector would plant none."""
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import detect, paramset, pipeline

    detect.register_default_paramsets()
    session = new_animal().session()
    pipeline.lab.Lab.insert1({"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
                             skip_duplicates=True)
    pipeline.subject.Subject.insert1({"subject": session.subject, "sex": "M", "subject_description": "",
                                      "subject_birth_date": datetime.date(2020, 1, 1)})
    pipeline.Session.insert1(session.key)
    yield session.key, paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))
    (pipeline.Session & session.key).delete()


def _plant(key, validity_idx, trace, paramset_idx, labels, status="computed"):
    """One detection of `trace`, its runs tiling `labels`."""
    from wl_preproc.schema import detect

    row = {**key, "trace": trace, "validity_paramset_type": "eye_validity", "validity_paramset_idx": validity_idx,
           "paramset_type": "eye_detection", "paramset_idx": paramset_idx, "status": status,
           "n_samples": len(labels) if status == "computed" else None, "reason": "" if status == "computed" else "planted"}
    detect.EyeDetection.insert1(row, allow_direct_insert=True)
    if status == "computed":
        detect.EyeDetection.Run.insert(
            {**{name: row[name] for name in detect.EyeDetection.primary_key}, "run_index": index,
             "run_start": run.start, "run_stop": run.stop, "label": run.label.value}
            for index, run in enumerate(runs_from_labels(labels)))


def test_a_trace_is_blended_only_when_every_registered_detector_computed_it(planted):
    """Left: every registered detector, identical traces, so agreement is
    perfect. Right: one detector missing. Both-eyes: every detector refused."""
    from wl_preproc.schema import consensus

    key, validity_idx = planted
    registered = sorted(_registered())
    trace = [F] * 20 + [S] * 5 + [F] * 20
    for idx in registered:
        _plant(key, validity_idx, "left", idx, trace)
        _plant(key, validity_idx, "conjunction", idx, trace, status="refused")
    for idx in registered[1:]:
        _plant(key, validity_idx, "right", idx, trace)

    consensus.DetectionQuality.populate(key)
    rows = (consensus.DetectionQuality & key).to_dicts()
    assert sorted((row["trace"], row["pso_as"]) for row in rows) == [("left", "fixation"), ("left", "saccade")]
    assert all(row["value"] == pytest.approx(1.0) and row["n_samples_compared"] == 45 for row in rows)


def test_a_session_with_no_complete_trace_is_not_a_candidate(planted):
    from wl_preproc.schema import consensus

    key, validity_idx = planted
    registered = sorted(_registered())
    trace = [F] * 20 + [S] * 5 + [F] * 20
    for idx in registered[1:]:
        _plant(key, validity_idx, "left", idx, trace)
    for idx in registered:
        _plant(key, validity_idx, "right", idx, trace, status="refused")

    assert len(consensus.DetectionQuality().key_source & key) == 0
    consensus.DetectionQuality.populate(key)
    assert len(consensus.DetectionQuality & key) == 0


def test_a_trace_with_no_saccades_is_undefined_and_stored_as_null(planted):
    """Every detector labels the whole trace fixation, as for a session the
    animal slept through: no disagreement is expected, so the score is
    undefined. NULL, never 1.0."""
    from wl_preproc.schema import consensus

    key, validity_idx = planted
    for idx in sorted(_registered()):
        _plant(key, validity_idx, "left", idx, [F] * 45)
    consensus.DetectionQuality.populate(key)
    rows = (consensus.DetectionQuality & key).to_dicts()
    assert sorted((row["pso_as"], row["value"], row["n_samples_compared"]) for row in rows) == [
        ("fixation", None, 45), ("saccade", None, 45)]


def test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for(planted):
    """Its detection can only ever error, so waiting for it would stop the
    table for every later session (spec amendment 4). Found in the full
    suite, where another module leaves such a paramset registered. A
    detection it computed before its detector was removed is not blended
    either."""
    from wl_preproc.schema import consensus, paramset

    key, validity_idx = planted
    registered = sorted(_registered())
    gone = paramset.register("eye_detection", {"detector": "not_a_detector_quality"})
    try:
        trace = [F] * 20 + [S] * 5 + [F] * 20
        for idx in [*registered, gone]:
            _plant(key, validity_idx, "left", idx, trace)
        consensus.DetectionQuality.populate(key)
        rows = (consensus.DetectionQuality & key).to_dicts()
        assert sorted((row["trace"], row["pso_as"]) for row in rows) == [("left", "fixation"), ("left", "saccade")]
        assert {row["detectors"] for row in rows} == {",".join(str(idx) for idx in registered)}
    finally:
        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": gone}).delete()


def test_a_second_paramset_for_one_detector_is_neither_waited_for_nor_blended(planted):
    """A detector's defaults changed leave its older paramset registered, and
    `EyeDetection` runs both. One detector is one rater (spec amendment 5):
    its default paramset is blended, the other is not, and the other is not
    waited for either, since an older paramset may only ever error. Left:
    the second paramset's detection disagrees with every other; right: it
    has none (the final review's I2)."""
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.schema import consensus, detect, paramset

    key, validity_idx = planted
    registered = sorted(_registered())
    defaults = detect._eye_detection_params(get_detector("engbert_kliegl"))
    older = paramset.register("eye_detection", {**defaults, "lambda_": defaults["lambda_"] + 1.0})
    try:
        trace = [F] * 20 + [S] * 5 + [F] * 20
        for idx in registered:
            _plant(key, validity_idx, "left", idx, trace)
            _plant(key, validity_idx, "right", idx, trace)
        _plant(key, validity_idx, "left", older, [S] * 45)
        consensus.DetectionQuality.populate(key)
        rows = (consensus.DetectionQuality & key).to_dicts()
        assert sorted((row["trace"], row["pso_as"]) for row in rows) == [
            ("left", "fixation"), ("left", "saccade"), ("right", "fixation"), ("right", "saccade")]
        assert {row["detectors"] for row in rows} == {",".join(str(idx) for idx in registered)}
        assert all(row["value"] == pytest.approx(1.0) for row in rows)
    finally:
        (paramset.ParamSet & {"paramset_type": "eye_detection", "paramset_idx": older}).delete(
            part_integrity="cascade")


def test_nothing_is_blended_while_a_detectors_default_paramset_is_unregistered(planted, monkeypatch):
    """A detector the code has but whose default paramset is not registered
    yet has run on nothing, so blending the others would be a partial set
    (spec section 3). The daemon registers the defaults before it populates,
    so this lasts no longer than one pass."""
    import dataclasses

    from wl_preproc.eye.detect import registry
    from wl_preproc.schema import consensus

    key, validity_idx = planted
    trace = [F] * 20 + [S] * 5 + [F] * 20
    for idx in sorted(_registered()):
        _plant(key, validity_idx, "left", idx, trace)
    monkeypatch.setitem(registry.DETECTORS, "unregistered_quality",
                        dataclasses.replace(registry.DETECTORS["engbert_kliegl"], name="unregistered_quality"))
    consensus.DetectionQuality.populate(key)
    assert len(consensus.DetectionQuality & key) == 0


def test_a_blended_detection_deleted_takes_its_scores_with_it(planted):
    """Re-detecting a session means deleting a detection and computing it
    again. The scores blended from it must go with it, or the session keeps
    a score of labels that no longer exist and is never blended again, since
    DataJoint never revisits a populated key (the final review's I1). A
    plain delete refuses, naming the remedy; `part_integrity="cascade"`
    takes the scores too, and the next populate blends the new labels."""
    import datajoint as dj

    from wl_preproc.schema import consensus, detect

    key, validity_idx = planted
    registered = sorted(_registered())
    trace = [F] * 20 + [S] * 5 + [F] * 20
    for idx in registered:
        _plant(key, validity_idx, "left", idx, trace)
    consensus.DetectionQuality.populate(key)
    assert [row["value"] for row in (consensus.DetectionQuality & key).to_dicts()] == [pytest.approx(1.0)] * 2

    redetected = detect.EyeDetection & key & {"paramset_type": "eye_detection", "paramset_idx": registered[0]}
    with pytest.raises(dj.DataJointError, match="part_integrity"):
        redetected.delete()
    redetected.delete(part_integrity="cascade")
    assert len(consensus.DetectionQuality & key) == 0

    _plant(key, validity_idx, "left", registered[0], [S] * 45)
    consensus.DetectionQuality.populate(key)
    rows = (consensus.DetectionQuality & key).to_dicts()
    assert len(rows) == 2 and all(row["value"] < 1.0 for row in rows)


_QUALITY_PROBE = """
import json
import os
import sys
from pathlib import Path

import datajoint as dj

sys.path.insert(0, os.getcwd())

from wl_preproc.schema._compat import apply_datajoint_compat

apply_datajoint_compat()
dj.config["database.host"] = os.environ["WLPP_PROBE_HOST"]
dj.config["database.port"] = int(os.environ["WLPP_PROBE_PORT"])
dj.config["database.user"] = os.environ["WLPP_PROBE_USER"]
dj.config["database.password"] = os.environ["WLPP_PROBE_PASSWORD"]
dj.config["safemode"] = False
dj.logger.setLevel("ERROR")

from wl_preproc import daemon
from wl_preproc.cli.main import main
from wl_preproc.schema import consensus

from tests.identities import new_animal
from tests.schema.test_detect_populate import _build_stepped_session


class _Factory:
    def __init__(self, root):
        self.root = Path(root)

    def mktemp(self, name):
        made = self.root / name
        made.mkdir(parents=True, exist_ok=True)
        return made


prefix = os.environ["WLPP_PROBE_PREFIX"]
# Activation only. Nothing here registers a paramset: that is the question.
daemon.activate_all(prefix=prefix)
session = new_animal().session()
_build_stepped_session(
    _Factory(os.environ["WLPP_PROBE_ROOT"]), dirname="probe", session_id=session.session_id,
    subject=session.subject, session_datetime=session.session_datetime, seed=1102,
)
assert main(["daemon", "--prefix", prefix]) == 0
rows = consensus.DetectionQuality().to_dicts()
print("PROBE " + json.dumps({"rows": len(rows), "traces": sorted({row["trace"] for row in rows})}))
"""


def test_a_real_wlpp_daemon_pass_writes_quality_rows_registering_nothing_itself(dj_conn, tmp_path):
    """`wlpp daemon`, the real entry point, against a prefix where nothing has
    ever registered a paramset: a row for each trace, metric and convention.
    It fails if the table is not a daemon stage or runs before the
    detections it blends."""
    import json
    import os
    import subprocess
    import sys

    import datajoint as dj

    from wl_preproc.eye.detect.consensus import BLENDED_METRICS
    from wl_preproc.schema import consensus

    result = subprocess.run(
        [sys.executable, "-c", _QUALITY_PROBE], capture_output=True, text=True,
        env={**os.environ,
             "WLPP_PROBE_HOST": str(dj.config["database.host"]),
             "WLPP_PROBE_PORT": str(dj.config["database.port"]),
             "WLPP_PROBE_USER": str(dj.config["database.user"]),
             "WLPP_PROBE_PASSWORD": str(dj.config["database.password"]),
             "WLPP_PROBE_PREFIX": "dq_",
             "WLPP_PROBE_ROOT": str(tmp_path),
             "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    marker = next((line for line in result.stdout.splitlines() if line.startswith("PROBE ")), None)
    assert marker is not None, f"probe printed no result:\n{result.stdout}\n{result.stderr}"
    assert json.loads(marker[len("PROBE "):]) == {
        "rows": len(TRACES) * len(BLENDED_METRICS) * len(consensus.PSO_AS_VALUES), "traces": sorted(TRACES)}
