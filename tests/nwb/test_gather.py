"""`gather.py` and `build.py`'s pure helpers: a file's identity and place,
and the agreement rows (design spec `2026-09-28-nwb-builder-design.md`
sections 3 and 6; the final review's findings C1 and I1)."""

from __future__ import annotations

import datetime
import math
from pathlib import Path


def test_two_subjects_sharing_a_session_id_get_different_files():
    """The final review's C1: a session id is the sync box's date and index,
    not scoped to a subject (`archive/stage.py::nas_root_for_subject`), so
    the identifier and the path both carry the subject."""
    from wl_preproc.nwb.build import nwb_path
    from wl_preproc.nwb.gather import identifier_for

    key = {"session_datetime": datetime.datetime(2027, 1, 12, 9, 0), "montage_id": 0, "activation_id": 0}
    first = identifier_for({**key, "subject": "monkeyA"}, "2027-01-12_01")
    second = identifier_for({**key, "subject": "monkeyB"}, "2027-01-12_01")
    assert first == "monkeyA.2027-01-12_01.montage-0.activation-0"
    assert first != second
    root = Path("/nwb")
    assert nwb_path(root, "monkeyA", "2027-01-12_01", first) == root / "monkeyA" / "2027-01-12_01" / f"{first}.nwb"
    assert nwb_path(root, "monkeyA", "2027-01-12_01", first) != nwb_path(root, "monkeyB", "2027-01-12_01", second)


def test_an_undefined_agreement_score_is_written_as_nan():
    """The final review's I1: `DetectorAgreement.value` is NULL where the
    metric is undefined (nothing comparable, or kappa's 0/0), and one such
    row must not fail the whole file. A pair with an unregistered detector
    is left out, as before."""
    from wl_preproc.nwb.gather import agreement_rows

    row = {"paramset_a": 1, "paramset_b": 2, "trace": "left", "metric": "kappa", "vocabulary": "kind",
           "pso_as": "saccadic", "value": None, "n_samples_compared": 0}
    (out,) = agreement_rows([row, {**row, "paramset_b": 99}], {1: "engbert_kliegl", 2: "remodnav"})
    assert (out["detector_a"], out["detector_b"]) == ("engbert_kliegl", "remodnav")
    assert math.isnan(out["value"]) and out["n_samples_compared"] == 0
