"""A long session's detection rows are stored in batches (scaling design spec, amendment 8).

The 12-hour run found it: each detector's `EyeDetection.Run` rows went to MySQL as one statement, and past MySQL 8's
default `max_allowed_packet` (64 MB) the server refused it -- error 1153, five detectors of six, every trace, no row
kept and every pass computing them again. Here the server's limit is lowered to 256 KB so a short session's rows stand
in for a 12-hour session's.
"""

from __future__ import annotations

import contextlib
import dataclasses

import numpy as np
import pytest

from tests.identities import new_animal
from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams

_SMALL_PACKET = 256 * 1024


@contextlib.contextmanager
def _packet_limit(conn, limit: int):
    """The server's `max_allowed_packet` set to `limit` for a fresh session, then put back. The session value is
    read-only and taken from the global at connect, so the connection reconnects each way."""
    (held,) = conn.query("SELECT @@GLOBAL.max_allowed_packet").fetchone()
    conn.query(f"SET GLOBAL max_allowed_packet = {limit}")
    conn.connect()
    try:
        yield
    finally:
        conn.query(f"SET GLOBAL max_allowed_packet = {held}")
        conn.connect()


def _alternating(gaze_deg, velocity_deg_s, available, fs_hz, params: EngbertKlieglParams):
    """Engbert-Kliegl's place in the registry: a three-sample saccade at every other three samples of what is
    available, the stretches between left unlabelled -- thousands of runs from a short trace."""
    from wl_preproc.eye.detect.labels import Label, Run

    runs, start = [], None
    usable = np.array([value is None for value in available])
    for index in range(len(usable) + 1):
        if index < len(usable) and usable[index]:
            if start is None:
                start = index
            continue
        if start is not None:
            for at in range(start, index - 2, 6):
                runs.append(Run(at, at + 3, Label.SACCADE))
            start = None
    return runs


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


def test_rows_beyond_one_statement_are_all_stored(daemon_module, dj_conn, prefix, tmp_path_factory, monkeypatch):
    from tests.schema.test_detect_populate import _build_stepped_session
    from wl_preproc.eye.detect import registry
    from wl_preproc.schema import detect

    animal = new_animal().session()
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory, dirname="longrows", session_id=animal.session_id, subject=animal.subject,
        session_datetime=animal.session_datetime, seed=1611)
    daemon_module.run_once(prefix=prefix)

    ek = detect.register_default_paramsets()["engbert_kliegl"]
    restriction = {**session_key, "paramset_idx": ek}
    (detect.EyeDetection & restriction).delete(prompt=False, part_integrity="cascade")
    monkeypatch.setitem(registry.DETECTORS, "engbert_kliegl",
                        dataclasses.replace(registry.DETECTORS["engbert_kliegl"], run=_alternating))
    monkeypatch.setattr(detect, "INSERT_CHUNK_ROWS", 200)

    with _packet_limit(dj_conn, _SMALL_PACKET):
        detect.EyeDetection.populate(restriction, suppress_errors=False)

    rows = {row["trace"]: row for row in (detect.EyeDetection & restriction).to_dicts()}
    assert set(rows) == {"left", "right", "conjunction"}
    assert {row["status"] for row in rows.values()} == {"computed"}
    for trace in ("left", "right"):
        stored = len(detect.EyeDetection.Run & restriction & {"trace": trace})
        assert stored > 2_000  # some hundreds of KB as one statement: refused at this limit unbatched


def test_a_batch_fits_the_tighter_default_limit():
    """`INSERT_CHUNK_ROWS` of the widest `EyeDetection.Run` row -- 21 columns, every one a double at its longest
    text -- fits pymysql's 16 MB, the tighter of its default and MySQL 8's 64 MB."""
    from wl_preproc.schema import detect

    widest = len(repr(tuple([-1.2345678901234567e-308] * 21)))
    assert detect.INSERT_CHUNK_ROWS * widest < 16 * 2**20
