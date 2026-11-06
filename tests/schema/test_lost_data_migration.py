"""A database declared before lost data and the screen region (design spec
`2026-11-06-eye-lost-data-and-region-design.md` section 8): DataJoint adds no
column to an existing table, alters no enum and reads a table's columns once
per process, so `EyeValidity` and `EyeDetection` refuse before any job is
reserved and name the statements that bring the tables to what DataJoint
declares now."""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def detect_schema(dj_conn, prefix):
    from wl_preproc.schema import detect

    detect.activate(prefix=prefix)
    return detect


def test_a_database_declared_before_masks_nothing_and_names_the_statements_and_the_deletion(detect_schema,
                                                                                            monkeypatch):
    assert not detect_schema._lacks_region()
    monkeypatch.setattr(detect_schema, "_lacks_region", lambda: True)

    with pytest.raises(RuntimeError) as raised:
        detect_schema.EyeValidity.populate()

    assert all(statement in str(raised.value) for statement in detect_schema._region_statements())
    assert "restarted" in str(raised.value)
    assert "delete the eye_validity paramsets whose params name no max_lost_ms" in str(raised.value)


def test_a_database_declared_before_detects_nothing_and_names_the_statements(detect_schema, monkeypatch):
    assert not detect_schema._lacks_lost_columns()
    monkeypatch.setattr(detect_schema, "_lacks_lost_columns", lambda: True)

    with pytest.raises(RuntimeError) as raised:
        detect_schema.EyeDetection.populate()

    assert all(statement in str(raised.value) for statement in detect_schema._lost_statements())


def test_a_label_read_without_lost_is_a_database_declared_before(detect_schema, monkeypatch):
    from types import SimpleNamespace

    label = detect_schema.EyeDetection.Run.heading.attributes["label"]
    old = SimpleNamespace(names=detect_schema.EyeDetection.Run.heading.names,
                          attributes={"label": SimpleNamespace(type=label.type.replace(",'lost'", ""))})
    monkeypatch.setattr(detect_schema.EyeDetection.Run, "_heading", old)

    assert detect_schema._lacks_lost_columns()


@pytest.mark.parametrize(("table", "columns", "statements"), [
    ("EyeValidity", "_REGION_COLUMNS", "_region_statements"),
    ("EyeDetection", "_LOST_COLUMNS", "_lost_statements"),
])
def test_the_statements_restore_the_tables_as_datajoint_declares_them(detect_schema, table, columns, statements):
    """Each table copied, taken back to what a database declared before held
    (the new columns dropped, the label without `lost`), and brought forward
    by the named statements: type, nullability, default, comment and place
    match what declaring them wrote, and no row of the tables is touched."""
    import datajoint as dj

    master = getattr(detect_schema, table)
    added = master if table == "EyeValidity" else master.Run
    names = {added.full_table_name: added.full_table_name[:-1] + '_restore_check`',
             master.Run.full_table_name: master.Run.full_table_name[:-1] + '_restore_label`'}

    def described(name):
        return [row[:2] + row[3:6] + row[8:9] for row in dj.conn().query(f"SHOW FULL COLUMNS FROM {name}").fetchall()]

    old_enum = master.Run.heading.attributes["label"].type.replace(",'lost'", "")
    for original, copy in names.items():
        dj.conn().query(f"CREATE TABLE {copy} LIKE {original}")
    try:
        dj.conn().query(f"ALTER TABLE {names[added.full_table_name]} "
                        + ", ".join(f"DROP COLUMN {column}" for column in getattr(detect_schema, columns)))
        dj.conn().query(f"ALTER TABLE {names[master.Run.full_table_name]} MODIFY COLUMN label {old_enum} NOT NULL")
        for statement in getattr(detect_schema, statements)():
            for original, copy in names.items():
                statement = statement.replace(original, copy)
            dj.conn().query(statement)
        restored = {original: described(copy) for original, copy in names.items()}
    finally:
        for copy in names.values():
            dj.conn().query(f"DROP TABLE IF EXISTS {copy}")
    assert restored == {original: described(original) for original in names}
