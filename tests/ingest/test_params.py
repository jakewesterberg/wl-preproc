"""Parameters that travelled with the raw data.

Spec section 5.4's requirement is that unknown keys are REJECTED — "so `nblocks`
vs `n_blocks` fails loudly rather than silently defaulting". Silently defaulting
is the failure this whole file exists to prevent.
"""

from __future__ import annotations

import pytest

from wl_preproc.contracts.paths import SessionLayout
from wl_preproc.ingest.params import PARAMS_FILENAME, register_session_params
from wl_preproc.schema import paramset
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def layout(tmp_path, dj_conn, prefix):
    paramset.activate(prefix=prefix)
    generate_session(tmp_path, CI_RECIPE)
    return SessionLayout(tmp_path, CI_RECIPE.session_id), prefix


def test_no_params_file_is_not_an_error(layout):
    """Most sessions carry none, and lab defaults are the ordinary case."""
    session_layout, prefix = layout

    assert register_session_params(session_layout, prefix=prefix) is None


def test_a_valid_file_registers_and_returns_an_id(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 4\n"
    )

    paramset_id = register_session_params(session_layout, prefix=prefix)

    assert isinstance(paramset_id, int)


def test_an_unknown_top_level_key_is_rejected(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 4\nnblocks: 4\n"
    )

    with pytest.raises(ValueError):
        register_session_params(session_layout, prefix=prefix)


def test_a_nested_unknown_key_inside_params_is_not_rejected(layout):
    """The same `nblocks`-for-`n_blocks` typo, but *inside* `params` rather than
    beside it, is a materially different case and is deliberately let through.

    `extra="forbid"` on `SessionParams` governs the envelope only — the two
    keys `paramset_type` and `params` — because `params` is typed as a bare
    `dict`, not a nested model. Section 5.3 describes one `ParamSet` table
    shared by preprocessing, artifact removal, clustering, LFP, MUA and eye
    detection, each with its own parameter shape, and nothing anywhere in this
    codebase (checked: `wl_preproc/schema/paramset.py` stores `params` as an
    opaque content-hashed blob; no per-`paramset_type` schema exists) records
    which keys are "known" for a given `paramset_type`. Rejecting an "unknown"
    key inside `params` would mean rejecting against a reference set this
    module does not have and cannot invent without silently making up a policy
    no other component agrees with.

    This is not a gap the two spec passages describing this behaviour hint at
    either: both the parent spec's section 5.4 and the ingest sub-project's own
    section 10 state the "`nblocks` vs `n_blocks`" example, and both do so with
    `nblocks` sitting beside `params` in the illustrating YAML, i.e. as the
    exact envelope-level typo `test_an_unknown_top_level_key_is_rejected` above
    already exercises — not as a typo living inside `params`'s own contents.
    Catching *that* case would need a schema keyed by `paramset_type`, which is
    a real, larger feature with no interface, fixture or open item anywhere in
    this sub-project asking for it; inventing one here, unreviewed, would risk
    quietly rejecting a legitimate paramset shape this module simply has not
    been told about.
    """
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  nblocks: 4\n"
    )

    paramset_id = register_session_params(session_layout, prefix=prefix)

    assert isinstance(paramset_id, int)


def test_registering_the_same_content_twice_returns_the_same_id(layout):
    """Paramset identity is its content hash, which is why nothing is lost by
    registering here and submitting the request later."""
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text(
        "paramset_type: clustering\nparams:\n  n_blocks: 7\n"
    )

    first = register_session_params(session_layout, prefix=prefix)
    second = register_session_params(session_layout, prefix=prefix)

    assert first == second


def test_malformed_yaml_raises_rather_than_defaulting(layout):
    session_layout, prefix = layout
    (session_layout.dir / PARAMS_FILENAME).write_text("{{{ not yaml")

    with pytest.raises(ValueError):
        register_session_params(session_layout, prefix=prefix)
