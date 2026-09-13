# tests/schema/test_harness.py
"""The database harness, and the type spellings every later task depends on."""

import datajoint as dj
import numpy as np
import pytest


def test_connection_is_live(dj_conn):
    assert dj_conn.is_connected


def test_datajoint_is_2x(dj_conn):
    major = int(dj.__version__.split(".")[0])
    assert major >= 2, f"expected DataJoint 2.x, got {dj.__version__}"


def test_schema_shim_is_applied(dj_conn):
    """Elements still call the lowercase dj.schema, which 2.x removed."""
    assert hasattr(dj, "schema")
    assert dj.schema is dj.Schema


# The spellings every later task uses. If one of these is wrong, it is wrong
# here, once, rather than in each schema module.
TYPE_VOCABULARY = {
    "an_int": "int",
    "a_small_int": "tinyint",
    "an_unsigned_int": "int unsigned",
    "a_float": "float",
    "a_double": "double",
    "a_string": "varchar(64)",
    "a_datetime": "datetime",
    "a_date": "date",
    "an_enum": "enum('a','b')",
    "a_blob": "<blob>",
}


def test_every_type_spelling_this_project_uses_declares(dj_conn):
    schema = dj.Schema("vocab_probe")
    attrs = "\n    ".join(f"{name} : {spec}" for name, spec in TYPE_VOCABULARY.items())

    @schema
    class Vocab(dj.Manual):
        definition = f"""
        # every attribute spelling this project relies on
        n : int
        ---
        {attrs}
        """

    assert set(TYPE_VOCABULARY) <= set(Vocab.heading.names)
    schema.drop()


def test_blob_round_trips_as_an_array(dj_conn):
    """The constraint the whole guardrail rests on: <blob> preserves an array,
    a bare longblob silently does not."""
    schema = dj.Schema("blob_probe")

    @schema
    class Payload(dj.Manual):
        definition = """
        # <blob> round-trip probe
        n : int
        ---
        arr : <blob>
        """

    # Pins the metadata signal test_guardrails.py's declaration check depends
    # on. `attr.is_blob` is true for `<blob>` AND for a bare `longblob` alike
    # (it reflects the physical MySQL column category, not codec attachment),
    # so the declaration guard keys on `attr.codec` instead. That distinction
    # is only real if `<blob>` actually attaches a codec on this DataJoint
    # version -- pinned here the same way the data-level round-trip below is
    # pinned, so a future DataJoint that stops doing this fails loudly instead
    # of silently disarming the guard the way `is_blob` already did once.
    assert Payload.heading["arr"].codec is not None, (
        "a <blob> attribute has no codec attached: DataJoint's behaviour "
        "changed, and the declaration guard in "
        "tests/schema/test_guardrails.py::test_no_table_declares_a_bare_longblob "
        "-- which keys on `codec is None` -- should be revisited"
    )

    arr = np.arange(2048, dtype=np.float32).reshape(32, 64)
    Payload.insert1({"n": 1, "arr": arr})
    got = (Payload & "n=1").fetch1("arr")
    assert isinstance(got, np.ndarray)
    assert got.shape == arr.shape and got.dtype == arr.dtype
    assert np.array_equal(got, arr)
    schema.drop()


def test_a_bare_longblob_refuses_an_array_instead_of_corrupting_it(dj_conn):
    """Pinned as an executable statement of WHY <blob> is mandatory.

    **DataJoint 2.3.3 changed this, and the rule was revisited rather than
    assumed** -- which is what the previous version of this test asked for in
    so many words: "if a future DataJoint makes bare longblob safe again, this
    test fails and the rule can be revisited deliberately rather than by
    assumption." It did, it failed, and this is the revisit.

    Under 2.3.2 a bare `longblob` accepted an ndarray and returned something
    that was not one -- silent corruption, and the test was named for it. Under
    2.3.3 `insert1` raises `DataJointError` naming the attribute and telling
    you to declare `<blob>`. The failure mode moved from silent to loud.

    **The <blob> rule stands, and its justification is what changed.** It is no
    longer "a bare longblob corrupts your data"; it is "a bare longblob refuses
    it at insert time, on a real session, after the pipeline has already done
    the work". `tests/schema/test_guardrails.py::test_no_table_declares_a_bare_
    longblob` still catches it at DECLARATION, which is earlier than 2.3.3's
    own error and earlier than any recording, so that guardrail is worth
    strictly more than the version bump, not less.

    The `codec is None` half of the pin is unchanged by 2.3.3 -- that is the
    metadata signal the declaration guard actually keys on, and it is asserted
    below for exactly that reason.

    Caught by CI on 2026-09-13, not locally: the constraint is
    `datajoint>=2.3,<3`, the development venv was resolved at 2.3.2, and CI
    resolves fresh. A green local run said nothing about the version the
    preprocessing server will install.
    """
    schema = dj.Schema("longblob_probe")

    @schema
    class Bare(dj.Manual):
        definition = """
        # deliberately wrong, to pin the failure mode
        n : int
        ---
        arr : longblob
        """

    # The other half of the pin above: a bare `longblob` must have no codec,
    # which is the metadata signal test_guardrails.py's declaration check
    # actually relies on (not `is_blob`, which is true here too and cannot
    # discriminate the two cases -- see that test for the full story).
    assert Bare.heading["arr"].codec is None, (
        "a bare longblob attribute has a codec attached: DataJoint's behaviour "
        "changed, and the declaration guard in "
        "tests/schema/test_guardrails.py::test_no_table_declares_a_bare_longblob "
        "-- which keys on `codec is None` -- should be revisited"
    )

    arr = np.arange(2048, dtype=np.float32)
    with pytest.raises(dj.errors.DataJointError, match="arr"):
        Bare.insert1({"n": 1, "arr": arr})

    # The row must not have landed. `insert1` raising is only half the
    # guarantee -- a partial write that raised on the way out would be worse
    # than the silent corruption this replaces, because nothing downstream
    # would know to look.
    assert len(Bare & "n=1") == 0, (
        "a bare longblob raised on insert but stored a row anyway: the failure "
        "is no longer silent but it is still corruption, and the <blob> "
        "guardrail in tests/schema/test_guardrails.py is now load-bearing in a "
        "way this test does not describe"
    )
    schema.drop()
