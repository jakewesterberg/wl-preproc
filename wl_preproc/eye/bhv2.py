"""MonkeyLogic's `.bhv2` behavioural log -- read only for eye calibration and
`ScreenInfo`. Nothing else: `BehavioralCodes`, `AnalogData` and trial
structure are walked past unread, because parent design spec section 4.5
puts this file second in the calibration fallback chain, behind the code
stream this pipeline already trusts more.

**No sample `.bhv2` was obtainable for this task -- the one exception in this
plan.** Every fact below is cited to where it was checked, not assumed; see
`tests/eye/test_bhv2.py` for the fixture that follows from that (a round trip
against this reader's own writer, not against a real file).

**Container format -- verified against
<https://monkeylogic.nimh.nih.gov/docs_BHV2BinaryStructure.html>, fetched
2026-08-30.** (The site's TLS chain is served incomplete --
`openssl s_client -connect monkeylogic.nimh.nih.gov:443 -showcerts` returns
verify code 21, "unable to verify the first certificate", because the
leaf's issuing intermediate is never sent by the server. The leaf itself is
a genuine `CN=*.nimh.nih.gov` certificate from a real CA (GoDaddy) valid for
the current date, so this was read as an ordinary server misconfiguration --
not a spoofed host -- and the page was fetched anyway.)

- The file is headerless: it begins directly with a sequence of variable
  blocks and simply ends at EOF. No block count is stored anywhere.
- Each block has 6 header fields; the 1st, 3rd and 5th give the lengths of
  the 2nd, 4th and 6th (the page's own wording).
- For MATLAB primitive types, the block's data follows the 6 header fields
  "in column-major order" (the page's own wording, with a worked 2x2
  `double` example).
- `struct` has one extra `uint64` field (count of struct fields) after the 6
  header fields, then, for each array element in column-major order, for
  each field in declared order, one full nested block (its own 6 header
  fields, recursively).
- `cell` has no extra field; each element, in column-major order, is one
  full nested block with an empty (0-length) name.
- A 0-sized dimension omits the block's data bytes entirely, though the
  header (including the `[0 0]` size field) is still written (the page's
  worked example, `A(2).b = ''`).

**Byte widths -- verified against NIMH ML's own reader/writer,
`mlbhv2.m::read_variable` / `write_variable`**, read from
<https://raw.githubusercontent.com/Doug1983/MonkeyLogic/master/mlbhv2.m> (a
GitHub mirror whose source matches this project's live docs by name --
`mlcalibrate.m` there implements exactly the `EyeCal` / `sig2deg` /
`translate` / `rotate` API `docs_CoordinateConversion.html` documents, and
the mirror ships real `.bhv2` samples the docs page alone does not):

- name length, type-tag length, dimension count: `uint64` (8 bytes each),
  little-endian assumed -- MATLAB's `fwrite` with no explicit machine format
  uses the host's native order, and NIMH ML's own install docs are
  Windows/x86-64-specific (a DirectX runtime dependency, a parallel-port
  driver installer, the Windows N media pack), so little-endian was assumed
  rather than observed. This is the one width/order claim here that only a
  real sample file could actually confirm.
- name and type-tag characters: 1 byte each (`fwrite(fid, name, 'char*1')`)
  -- NOT MATLAB's in-memory 2-byte `char`.
- the "size of variable" field (each dimension's length): **`uint64`, 8
  bytes each -- this CONTRADICTS `docs_BHV2BinaryStructure.html`**, whose
  own worked-example tables label this field `[1x2 double]`. `mlbhv2.m` is
  unambiguous on both the read side
  (`fread(obj.fid,[1 dim],'uint64=>double')`) and the write side
  (`fwrite(obj.fid,sz,'uint64')`), so this module follows the source over
  the page's own example labels. Flagged here for whoever next reads that
  page expecting the two to agree -- they do not.
- struct's extra "number of fields" field: `uint64`, 8 bytes.
- primitive data: MATLAB's own native width per class (`double` / `int64` /
  `uint64` = 8 bytes, `single` / `int32` / `uint32` = 4, `int16` / `uint16`
  = 2, `int8` / `uint8` / `logical` / `char` = 1) -- ordinary MATLAB class
  widths, not something either source needed to spell out further.

**Which top-level block, and which of its fields -- NOT found on either doc
page; inferred from `mlconfig.m` and `mlcalibrate.m` in the same mirror,
cross-checked against this project's own live docs pages
(`docs_CoordinateConversion.html`, `docs_ScreenObject.html`,
`docs_GettingStarted.html`).** This is the one part of this module that is a
judgment call rather than a citation, and it overrides this task's own
brief, which named the target block "ScreenInfo" -- a name this module could
not find anywhere. Grepping the entire mirror (`grep -rni screeninfo`, ~90
`.m` files) turns up nothing but the unrelated low-level
`mglgetscreeninfo` graphics function. `PixelsPerDegree` is instead a
*dependent* (computed-at-access) property of the `mlconfig` class
(`mlconfig.m`, `properties (Dependent = true)`), most plausibly frozen into
the saved value at write time since a data file has no live display adapter
left to recompute it from. `mlread`'s own signature --
`[data, MLConfig, TrialRecord, filename] = mlread(...)`
(`docs_GettingStarted.html`) -- confirms `MLConfig` is itself a real
top-level saved block, and `mlcalibrate.m`'s constructor reads
`MLConfig.PixelsPerDegree`, `MLConfig.EyeTransform` (`cell(1,3)`, one struct
per calibration method) and `MLConfig.EyeCalibration` (the active method,
1-3) directly. So this module looks for those three fields of `MLConfig`,
not for a `ScreenInfo` block.

**MonkeyLogic's own calibration is not always six numbers.** Method 1 ("Raw
Signal") stores an `offset` (2 doubles); method 2 ("Origin & Gain") stores
`origin`, `gain` and `rotation` (5 doubles, from `mlcalibrate.m`); method 3
("2-D Spatial Transformation") stores a `projective_transform` object
dominated by a function handle and fit data, essentially opaque to a reader
this narrow. `Bhv2Calibration.a` is therefore whatever plain `double`
fields this module finds as DIRECT children of the active method's struct,
in field order, concatenated -- not asserted to be six of anything.
`as_affine_map` is where a six-number `a` becomes usable and anything else
is declined; see its own docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wl_preproc.eye.calibration import AffineMap

_UINT64 = np.dtype("<u8")
_FLOAT64 = np.dtype("<f8")

# MATLAB's own native byte width per class, used only to skip a primitive
# field this module does not otherwise care about. `double` and `char` are
# the only two types this module ever materialises (numeric calibration
# content, and struct/cell element names and any char-typed struct field).
_PRIMITIVE_ITEMSIZE: dict[str, int] = {
    "int8": 1, "uint8": 1, "char": 1, "logical": 1,
    "int16": 2, "uint16": 2,
    "int32": 4, "uint32": 4, "single": 4,
    "int64": 8, "uint64": 8, "double": 8,
}

# See the module docstring's penultimate section for why these are "MLConfig"
# and its own fields, not the "ScreenInfo" name this task's brief used.
_TOP_LEVEL_BLOCK = "MLConfig"
_PIXELS_PER_DEGREE_FIELD = "PixelsPerDegree"
_EYE_CALIBRATION_FIELD = "EyeCalibration"
_EYE_TRANSFORM_FIELD = "EyeTransform"
_MLCONFIG_WANTED = frozenset(
    {_PIXELS_PER_DEGREE_FIELD, _EYE_CALIBRATION_FIELD, _EYE_TRANSFORM_FIELD}
)


class Bhv2Unreadable(ValueError):
    """The file exists but its structure could not be walked.

    Distinct from absence on purpose: a missing `.bhv2` is an ordinary skip of
    the fallback chain's step 2 (design spec section 4.5), while a present but
    unparseable one means either a format change or a corrupt transfer, and the
    two must not render identically in the daily report.
    """


@dataclass(frozen=True, slots=True)
class Bhv2Calibration:
    """What this reader found, or the fact that it found nothing.

    `present` tracks `a` specifically. MonkeyLogic's own calibration and its
    `PixelsPerDegree` come from the same `MLConfig` block but are otherwise
    independent facts -- a session can have one without the other -- so
    `pixels_per_degree` may be populated even when `present` is False, and
    vice versa. Task 7 (design spec section 4.5) only asks this reader for a
    calibration, which is what `present` answers.
    """

    present: bool
    a: tuple[float, ...] | None
    pixels_per_degree: float | None


def read_calibration(path: str | Path) -> Bhv2Calibration:
    """MonkeyLogic's own calibration and `PixelsPerDegree`, from `MLConfig`.

    A missing file is absence (`present=False`), not an error -- the
    fallback chain's own step 2 is allowed to be skipped (module docstring).
    A file that exists but cannot be structurally walked raises
    `Bhv2Unreadable` instead: that is a different fact a caller must be able
    to tell apart from absence. A file that walks fine but simply has no
    `MLConfig`, or an `MLConfig` without the fields this module looks for, is
    ALSO absence, not an error -- it parsed; it just did not have what we
    wanted, which is an ordinary outcome for, say, a joystick-only session.
    """
    try:
        buf = Path(path).read_bytes()
    except FileNotFoundError:
        return Bhv2Calibration(present=False, a=None, pixels_per_degree=None)

    fields = _walk(buf)

    pixels_per_degree: float | None = None
    a: tuple[float, ...] | None = None

    if fields is not None:
        ppd_value = fields.get(_PIXELS_PER_DEGREE_FIELD)
        if isinstance(ppd_value, tuple) and ppd_value:
            pixels_per_degree = float(ppd_value[0])

        a = _extract_calibration(fields)

    return Bhv2Calibration(present=a is not None, a=a, pixels_per_degree=pixels_per_degree)


def as_affine_map(cal: Bhv2Calibration) -> AffineMap | None:
    """`cal.a` as a borrowed `AffineMap`, if and only if it is exactly six
    numbers.

    MonkeyLogic's own calibration is not always six numbers (module
    docstring): Origin & Gain gives five, the 2-D Spatial Transformation
    method gives however many plain `double` fields happen to sit directly
    on an otherwise-opaque projective/polynomial object. Forcing a
    mismatched count into the documented `(a00, a01, b0, a10, a11, b1)`
    slots would silently misassign a gain term to an offset, or drop one
    entirely -- worse than declining. Task 7 (design spec section 4.5)
    treats `None` exactly like every other declined source and falls
    through to the next one.
    """
    if cal.a is None or len(cal.a) != 6:
        return None
    a0, a1, a2, a3, a4, a5 = (float(x) for x in cal.a)
    return AffineMap(a=(a0, a1, a2, a3, a4, a5))


def _extract_calibration(fields: dict[str, object]) -> tuple[float, ...] | None:
    """The active calibration method's own `double` fields, in field order.

    "Active" comes from `EyeCalibration` (1-3, MATLAB-indexed) selecting one
    of `EyeTransform`'s three cells (module docstring). Fields of that
    struct that are not plain `double` (a `char` note, a nested struct or
    cell) are not re-descended -- this module goes no deeper than the
    method's own immediate fields.
    """
    method_value = fields.get(_EYE_CALIBRATION_FIELD)
    transform_value = fields.get(_EYE_TRANSFORM_FIELD)
    if not (isinstance(method_value, tuple) and method_value):
        return None
    if not isinstance(transform_value, list):
        return None

    method_index = int(round(method_value[0])) - 1  # MATLAB is 1-based.
    if not (0 <= method_index < len(transform_value)):
        return None

    chosen = transform_value[method_index]
    if not (isinstance(chosen, list) and chosen and isinstance(chosen[0], dict)):
        return None  # the chosen cell was not a struct -- nothing to harvest

    harvested: list[float] = []
    for value in chosen[0].values():
        if isinstance(value, tuple) and value and all(isinstance(v, float) for v in value):
            harvested.extend(value)
    return tuple(harvested) if harvested else None


def _walk(buf: bytes) -> dict[str, object] | None:
    """Every top-level block, until EOF; `MLConfig`'s wanted fields if found.

    Every OTHER top-level block (`BehavioralCodes`, `AnalogData`, trial
    structure, and anything else) is walked structurally -- struct and cell
    blocks cannot be skipped by arithmetic alone, since nothing in the
    format records a compound block's total byte size (module docstring) --
    but never materialised into Python values. That is what "skip by its
    declared length without parsing it" means here for a compound block: its
    bytes are consumed, never its meaning.
    """
    offset = 0
    n = len(buf)
    mlconfig_fields: dict[str, object] | None = None
    while offset < n:
        offset, name, type_tag, dims = _read_header(buf, offset)
        if name == _TOP_LEVEL_BLOCK and type_tag == "struct":
            offset, nfield = _read_uint64(buf, offset)
            offset, elements = _read_struct_fields(
                buf, offset, nfield, _prod(dims), wanted=_MLCONFIG_WANTED
            )
            if elements:
                mlconfig_fields = elements[0]
        else:
            offset = _skip_value(buf, offset, type_tag, dims)
    return mlconfig_fields


def _require(buf: bytes, offset: int, nbytes: int) -> None:
    if offset + nbytes > len(buf):
        raise Bhv2Unreadable(
            f"declared length runs past end of file at byte offset {offset} "
            f"(need {nbytes} more bytes, {len(buf) - offset} remain)"
        )


def _read_uint64(buf: bytes, offset: int) -> tuple[int, int]:
    _require(buf, offset, 8)
    value = int(np.frombuffer(buf, dtype=_UINT64, count=1, offset=offset)[0])
    return offset + 8, value


def _read_uint64_array(buf: bytes, offset: int, count: int) -> tuple[int, tuple[int, ...]]:
    nbytes = 8 * count
    _require(buf, offset, nbytes)
    arr = np.frombuffer(buf, dtype=_UINT64, count=count, offset=offset)
    return offset + nbytes, tuple(int(x) for x in arr)


def _read_doubles(buf: bytes, offset: int, count: int) -> tuple[int, tuple[float, ...]]:
    # Already the column-major flattening the format specifies (module
    # docstring): the file's flat sequence of doubles for a variable IS its
    # column-major order, so no reshape/transpose is needed for the flat
    # tuples this module ever returns.
    nbytes = 8 * count
    _require(buf, offset, nbytes)
    arr = np.frombuffer(buf, dtype=_FLOAT64, count=count, offset=offset)
    return offset + nbytes, tuple(float(x) for x in arr)


def _read_chars(buf: bytes, offset: int, count: int) -> tuple[int, str]:
    _require(buf, offset, count)
    # latin-1: every byte value decodes, deliberately -- a name or type tag
    # this module does not recognise should fail by "not in the wanted set"
    # or "not a known type", not by crashing on the decode of a field this
    # module may not even care about.
    return offset + count, buf[offset : offset + count].decode("latin-1")


def _read_header(buf: bytes, offset: int) -> tuple[int, str, str, tuple[int, ...]]:
    """The 6 header fields common to every block, top-level or nested."""
    offset, name_len = _read_uint64(buf, offset)
    offset, name = _read_chars(buf, offset, name_len)
    offset, type_len = _read_uint64(buf, offset)
    offset, type_tag = _read_chars(buf, offset, type_len)
    offset, ndims = _read_uint64(buf, offset)
    offset, dims = _read_uint64_array(buf, offset, ndims)
    return offset, name, type_tag, dims


def _prod(dims: tuple[int, ...]) -> int:
    result = 1
    for d in dims:
        result *= d
    return result


def _skip_value(buf: bytes, offset: int, type_tag: str, dims: tuple[int, ...]) -> int:
    """Advance past one block's content without materialising any of it."""
    n_elements = _prod(dims)
    if type_tag == "struct":
        offset, nfield = _read_uint64(buf, offset)
        for _ in range(n_elements * nfield):
            offset, _name, child_type, child_dims = _read_header(buf, offset)
            offset = _skip_value(buf, offset, child_type, child_dims)
        return offset
    if type_tag == "cell":
        for _ in range(n_elements):
            offset, _name, child_type, child_dims = _read_header(buf, offset)
            offset = _skip_value(buf, offset, child_type, child_dims)
        return offset
    itemsize = _PRIMITIVE_ITEMSIZE.get(type_tag)
    if itemsize is None:
        raise Bhv2Unreadable(f"unrecognised type tag {type_tag!r} at byte offset {offset}")
    nbytes = n_elements * itemsize
    _require(buf, offset, nbytes)
    return offset + nbytes


def _materialize(buf: bytes, offset: int, type_tag: str, dims: tuple[int, ...]):
    """Decode one block's content into a Python value.

    `double` -> a flat `tuple[float, ...]`; `char` -> `str`; `struct` -> a
    list of per-array-element `{field_name: value}` dicts (recursively
    materialised in full -- this is only ever called on a block this module
    has already decided it wants, so everything beneath it is wanted too);
    `cell` -> a list of per-element values. Any other primitive is skipped
    by length and represented as `None` -- this module never needs an
    `int32` or a `logical`'s actual value.
    """
    n_elements = _prod(dims)
    if type_tag == "double":
        return _read_doubles(buf, offset, n_elements)
    if type_tag == "char":
        return _read_chars(buf, offset, n_elements)
    if type_tag == "struct":
        offset, nfield = _read_uint64(buf, offset)
        return _read_struct_fields(buf, offset, nfield, n_elements, wanted=None)
    if type_tag == "cell":
        elements: list[object] = []
        for _ in range(n_elements):
            offset, _name, child_type, child_dims = _read_header(buf, offset)
            offset, value = _materialize(buf, offset, child_type, child_dims)
            elements.append(value)
        return offset, elements
    itemsize = _PRIMITIVE_ITEMSIZE.get(type_tag)
    if itemsize is None:
        raise Bhv2Unreadable(f"unrecognised type tag {type_tag!r} at byte offset {offset}")
    nbytes = n_elements * itemsize
    _require(buf, offset, nbytes)
    return offset + nbytes, None


def _read_struct_fields(
    buf: bytes,
    offset: int,
    nfield: int,
    n_elements: int,
    *,
    wanted: frozenset[str] | None,
) -> tuple[int, list[dict[str, object]]]:
    """`n_elements` struct-array elements, `nfield` fields each, in the
    element-major, field-minor order the format writes them in (module
    docstring). A field whose name is not in `wanted` (when `wanted` is not
    None) is walked past, not decoded -- its value in the returned dict is
    `None`.
    """
    elements: list[dict[str, object]] = []
    for _ in range(n_elements):
        element: dict[str, object] = {}
        for _ in range(nfield):
            offset, name, type_tag, dims = _read_header(buf, offset)
            if wanted is None or name in wanted:
                offset, value = _materialize(buf, offset, type_tag, dims)
            else:
                offset = _skip_value(buf, offset, type_tag, dims)
                value = None
            element[name] = value
        elements.append(element)
    return offset, elements
