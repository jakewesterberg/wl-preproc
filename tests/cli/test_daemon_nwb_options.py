"""The daemon's NWB share options (design spec
`2026-09-29-nwb-publishing-design.md` section 3), refused at parse time when
given by halves, before anything touches the database."""

from __future__ import annotations

import pytest


def test_a_share_given_by_halves_is_refused(tmp_path, capsys):
    from wl_preproc.cli.main import main

    with pytest.raises(SystemExit) as raised:
        main(["daemon", "--nwb-slow-root", str(tmp_path)])
    assert raised.value.code == 2
    assert "--nwb-slow-share" in capsys.readouterr().err


def test_the_fast_share_needs_the_slow_one(tmp_path, capsys):
    from wl_preproc.cli.main import main

    with pytest.raises(SystemExit) as raised:
        main(["daemon", "--host", "wl-nas", "--nwb-fast-root", str(tmp_path), "--nwb-fast-share", "nvme"])
    assert raised.value.code == 2
    assert "long-term home" in capsys.readouterr().err
