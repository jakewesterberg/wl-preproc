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

    arr = np.arange(2048, dtype=np.float32).reshape(32, 64)
    Payload.insert1({"n": 1, "arr": arr})
    got = (Payload & "n=1").fetch1("arr")
    assert isinstance(got, np.ndarray)
    assert got.shape == arr.shape and got.dtype == arr.dtype
    assert np.array_equal(got, arr)
    schema.drop()


def test_a_bare_longblob_corrupts_silently(dj_conn):
    """Pinned as an executable statement of WHY <blob> is mandatory. If a future
    DataJoint makes bare longblob safe again, this test fails and the rule can
    be revisited deliberately rather than by assumption."""
    schema = dj.Schema("longblob_probe")

    @schema
    class Bare(dj.Manual):
        definition = """
        # deliberately wrong, to pin the failure mode
        n : int
        ---
        arr : longblob
        """

    arr = np.arange(2048, dtype=np.float32)
    Bare.insert1({"n": 1, "arr": arr})
    got = (Bare & "n=1").fetch1("arr")
    assert not isinstance(got, np.ndarray), (
        "a bare longblob round-tripped an array: DataJoint's behaviour changed, "
        "and the <blob> guardrail in tests/schema/test_guardrails.py should be revisited"
    )
    schema.drop()
