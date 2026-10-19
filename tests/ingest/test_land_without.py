"""Taking a session in without the sync box (design spec
`2026-10-18-training-records-and-backup-clock-design.md` section 5, decision 10,
section 8's part 3): a manifest that does not list it lands; one that expects
the rig's record lands once `xcon/DONE` is written; and a listed sync box whose
transfer never finishes stays stalled until a person lands the session without
it, by one command, recorded with who ran it, why and when."""

from __future__ import annotations

import datetime
import shutil

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.watcher import Outcome, scan_once
from wl_preproc.schema import core, ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session

LATER = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=3)


@pytest.fixture
def root(tmp_path, dj_conn, prefix):
    """A generated CI session; whatever it lands is deleted afterwards. A
    landed session with no sync box log would otherwise fail every later
    module's daemon pass on its clock (spec amendment 7's lesson)."""
    from wl_preproc.schema import pipeline

    ingest.activate(prefix=prefix)
    core.activate(prefix=prefix)
    generate_session(tmp_path, CI_RECIPE)
    layout = SessionLayout(tmp_path, CI_RECIPE.session_id)
    yield tmp_path, prefix, layout
    landed = (ingest.Ingestion & {"session_dir": str(layout.dir)}).proj().to_dicts()
    if landed:
        (pipeline.Session & landed).delete(prompt=False)
    (ingest.LandedWithout & {"session_dir": str(layout.dir)}).delete(prompt=False)


def _manifest(layout, subject, replace=()):
    """Land under its own subject (as `tests/ingest/test_watcher.py`'s
    `_use_dedicated_subject` does), with the manifest's text edited."""
    text = layout.manifest_path.read_text().replace(f"subject: {CI_RECIPE.subject}", f"subject: {subject}")
    for old, new in replace:
        assert old in text, old
        text = text.replace(old, new)
    layout.manifest_path.write_text(text)


def _landed(layout):
    return (ingest.Ingestion & {"session_dir": str(layout.dir)}).fetch1()


def test_a_session_whose_manifest_does_not_list_the_sync_box_lands(root):
    tmp_path, prefix, layout = root
    _manifest(layout, "nosync1", replace=(("- syncbox\n", ""),))
    shutil.rmtree(layout.system_dir("syncbox"))
    assert scan_once(tmp_path, prefix=prefix).outcomes[str(layout.dir)] is Outcome.INGESTED
    row = _landed(layout)
    assert (row["integrity"], row["topology"]["syncbox"]) == ("verified", "absent")
    assert "syncbox" not in (core.AcquisitionSystem & {"subject": "nosync1"}).to_arrays("system").tolist()


def test_a_session_expecting_the_rigs_record_lands_once_it_is_closed(root):
    tmp_path, prefix, layout = root
    _manifest(layout, "rigrec1", replace=(("expects_rig_record: false", "expects_rig_record: true"),))
    assert scan_once(tmp_path, prefix=prefix).outcomes[str(layout.dir)] is Outcome.INCOMPLETE
    assert scan_once(tmp_path, prefix=prefix, now=LATER).outcomes[str(layout.dir)] is Outcome.STALLED
    layout.rig_done_marker.write_text("")
    assert scan_once(tmp_path, prefix=prefix).outcomes[str(layout.dir)] is Outcome.INGESTED


def test_a_listed_sync_box_that_never_finishes_stays_stalled_until_a_person_lands_without_it(root):
    """Decision 10: the session waits for a person, who lands it without the
    sync box by one command; the command says who, why and when, and the
    session lands at once, its sync box waived: no AcquisitionSystem row, its
    partial files left in the folder."""
    from wl_preproc.ingest.without import land_without

    tmp_path, prefix, layout = root
    _manifest(layout, "stall1")
    layout.done_marker("syncbox").unlink()
    assert scan_once(tmp_path, prefix=prefix, now=LATER).outcomes[str(layout.dir)] is Outcome.STALLED
    outcome = land_without(layout.dir, "syncbox", actor="J. W.", reason="the Pi's card failed", prefix=prefix,
                           now=LATER)
    assert outcome is Outcome.INGESTED
    record = (ingest.LandedWithout & {"subject": "stall1"}).fetch1()
    assert (record["system"], record["actor"], record["reason"], record["session_dir"]) == (
        "syncbox", "J. W.", "the Pi's card failed", str(layout.dir))
    assert record["recorded_at"] == LATER.replace(tzinfo=None, microsecond=0)
    row = _landed(layout)
    assert (row["integrity"], row["topology"]["syncbox"]) == ("verified", "waived")
    assert "syncbox" not in (core.AcquisitionSystem & {"subject": "stall1"}).to_arrays("system").tolist()
    assert any(layout.system_dir("syncbox").iterdir())


def test_a_person_can_land_a_session_without_the_rigs_record(root):
    from wl_preproc.ingest.without import land_without

    _tmp_path, prefix, layout = root
    _manifest(layout, "rigrec2", replace=(("expects_rig_record: false", "expects_rig_record: true"),))
    assert land_without(layout.dir, "xcon", actor="J. W.", reason="the rig crashed", prefix=prefix) is (
        Outcome.INGESTED)
    assert _landed(layout)["topology"]["syncbox"] == "present"


@pytest.mark.parametrize("system, subject, expected", [
    ("bcam", "refuse1", "its transfer finished: its DONE marker landed, so there is nothing to land without"),
    ("rhs", "refuse2", "rhs is not listed in its manifest"),
    ("xcon", "refuse3", "its manifest does not expect the rig's record"),
    ("camera", "refuse4", "camera is neither a system nor xcon"),
])
def test_a_landing_without_that_changes_nothing_is_refused(root, system, subject, expected):
    from wl_preproc.ingest.without import Refused, land_without

    _tmp_path, prefix, layout = root
    _manifest(layout, subject)
    with pytest.raises(Refused, match=expected):
        land_without(layout.dir, system, actor="J. W.", reason="r", prefix=prefix)
    assert len(ingest.LandedWithout & {"subject": subject}) == 0


def test_a_session_already_landed_is_refused(root):
    from wl_preproc.ingest.without import Refused, land_without

    tmp_path, prefix, layout = root
    _manifest(layout, "refuse5")
    scan_once(tmp_path, prefix=prefix)
    with pytest.raises(Refused, match="it has already landed"):
        land_without(layout.dir, "syncbox", actor="J. W.", reason="r", prefix=prefix)


def test_a_session_whose_manifest_cannot_be_read_is_refused(root):
    from wl_preproc.ingest.without import Refused, land_without

    _tmp_path, prefix, layout = root
    layout.manifest_path.write_text("{not yaml")
    with pytest.raises(Refused, match="its manifest cannot be read"):
        land_without(layout.dir, "syncbox", actor="J. W.", reason="r", prefix=prefix)


def test_landing_without_twice_records_once(root):
    """A second run, the session still waiting for another system, finds the
    record already there and evaluates the session again."""
    from wl_preproc.ingest.without import land_without

    _tmp_path, prefix, layout = root
    _manifest(layout, "twice1")
    layout.done_marker("syncbox").unlink()
    layout.done_marker("bcam").unlink()
    assert land_without(layout.dir, "syncbox", actor="J. W.", reason="r", prefix=prefix) is Outcome.INCOMPLETE
    assert land_without(layout.dir, "syncbox", actor="A. N.", reason="again", prefix=prefix) is Outcome.INCOMPLETE
    assert (ingest.LandedWithout & {"subject": "twice1"}).fetch1("actor") == "J. W."


def test_the_command_lands_the_session_and_says_so(root, capsys):
    from wl_preproc.cli.main import main

    _tmp_path, prefix, layout = root
    _manifest(layout, "cli1")
    layout.done_marker("syncbox").unlink()
    assert main(["land-without", "--session", str(layout.dir), "--system", "syncbox", "--actor", "J. W.",
                 "--reason", "the Pi's card failed", "--prefix", prefix]) == 0
    assert f"{layout.dir}: landing without syncbox, by J. W.: ingested" in capsys.readouterr().out
    assert main(["land-without", "--session", str(layout.dir), "--system", "syncbox", "--actor", "J. W.",
                 "--reason", "r", "--prefix", prefix]) == 1
    assert "refusing: it has already landed" in capsys.readouterr().out
