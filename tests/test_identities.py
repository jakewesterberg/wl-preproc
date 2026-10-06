"""The test suite's fresh identities (`tests/identities.py`): an animal no
other test names, its sessions, probe serials and request keys."""

from __future__ import annotations

import datetime
import itertools
import re
from pathlib import Path

import pytest

from tests import identities


def test_each_animal_is_new_full_width_and_inside_no_other():
    """Some tests find their errors by an animal's name in the text, so no
    name may hold another."""
    from wl_preproc.ingest.landing import SUBJECT_MAX_LEN

    names = [identities.new_animal().subject for _ in range(3)]
    assert len(set(names)) == 3
    assert all(name.startswith(identities.PREFIX) and len(name) == SUBJECT_MAX_LEN for name in names)
    assert not [(a, b) for a, b in itertools.permutations(names, 2) if a in b]


def test_a_session_is_on_a_past_day_of_its_own_at_nine_named_for_its_date():
    """nwbinspector calls a future session start critical, and
    `test_spikeglx_restart.py::_session` lands at 09:00 on its id's date."""
    animal = identities.new_animal()
    first, second = animal.session(), animal.session()
    for session in (first, second):
        assert session.subject == animal.subject
        assert session.session_datetime.date() < datetime.datetime.now(datetime.UTC).date()
        assert session.session_datetime.hour == 9
        assert session.session_id == f"{session.date.isoformat()}_01"
        assert session.key == {"subject": animal.subject, "session_datetime": session.session_datetime}
    assert second.date > first.date


def test_a_session_on_a_day_already_used_is_an_hour_later_with_the_next_number():
    """Two sessions of one animal on one day, as same-day calibration
    carry-forward needs; and a day chosen by the test, future or past."""
    animal = identities.new_animal()
    first = animal.session()
    again = animal.session(on=first.date)
    assert (again.session_id, again.session_datetime) == (
        f"{first.date.isoformat()}_02", first.session_datetime.replace(hour=10))
    future = animal.session(on=datetime.date(2099, 1, 2))
    assert (future.session_id, future.session_datetime) == ("2099-01-02_01", datetime.datetime(2099, 1, 2, 9))
    later = animal.session()
    assert later.date not in (first.date, future.date) and later.session_id.endswith("_01")


def test_the_series_stops_before_it_reaches_today(monkeypatch):
    monkeypatch.setattr(identities, "_animals", itertools.count(10_000))
    with pytest.raises(RuntimeError, match="no past day is left"):
        identities.new_animal().session()


def test_serials_are_new_and_in_the_reserved_range():
    serials = [identities.new_serial() for _ in range(3)]
    assert len(set(serials)) == 3
    assert all(re.fullmatch(identities.SERIAL_PREFIX + r"\d{6}", serial) for serial in serials)


def test_a_request_key_is_named_for_its_animal():
    animal = identities.new_animal()
    assert animal.key("canonical") == f"{animal.subject}-canonical"


def test_no_test_writes_a_reserved_identity_by_hand():
    """So the helper's series stays free: a name or serial written into a
    test by hand could be one the helper hands out later in the same run."""
    reserved = re.compile(rf"\b(?:{identities.PREFIX}\d{{6}}|{identities.SERIAL_PREFIX}\d{{6}})\b")
    root = Path(__file__).parent
    found = [f"{path.relative_to(root)}:{number}: {line.strip()}"
             for path in sorted(root.rglob("*.py")) if path.name != "identities.py"
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
             if reserved.search(line)]
    assert found == []


def test_fresh_identities_land_as_sessions_of_their_own(dj_conn, prefix, tmp_path_factory):
    """Through the landing helper most database tests use: two sessions of
    one animal and one of another, each its own session with its own files."""
    from tests.schema.test_spikeglx_restart import _session
    from wl_preproc import daemon
    from wl_preproc.schema import ingest, pipeline

    daemon.activate_all(prefix=prefix)
    first_animal, second_animal = identities.new_animal(), identities.new_animal()
    wanted = [first_animal.session(), first_animal.session(), second_animal.session()]
    for session in wanted:
        _recipe, key = _session(tmp_path_factory, subject=session.subject, session_id=session.session_id,
                                probe_serial=identities.new_serial())
        assert key == session.key
    landed = (ingest.Ingestion & [session.key for session in wanted]).to_dicts()
    assert len(landed) == len(pipeline.Session & [session.key for session in wanted]) == 3
    assert len({row["session_dir"] for row in landed}) == 3
