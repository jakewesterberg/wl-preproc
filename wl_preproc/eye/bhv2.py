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

**MonkeyLogic's own calibration is not always six numbers, and method 2's real
shape is UNVERIFIED.** Method 1 ("Raw Signal") stores an `offset` (2
doubles) -- confirmed directly against a real file, below. Method 2 ("Origin
& Gain") does NOT verifiably store "origin, gain and rotation (5 doubles)":
an earlier version of this docstring made that claim from `mlcalibrate.m`
alone, which is wrong -- `mlcalibrate.m` only reads a SUBSET of the fields
the calibration-AUTHORING tool actually creates. `mlcalibrate_origin_gain.m`'s
own `init_tform()` initialises 16 fields: 14 plain `double`
(`operator_view`, `origin`, `gain`, `rotation`, `rotation_t`,
`rotation_rev_t`, `fixshape`, `fixcolor`, `fixsize`, `fixinterval`,
`windowsize`, `waittime`, `holdtime`, `jittertolerance` -- 24 numbers by
direct count), one `char` (`fiximage`), and one nested struct
(`RewardFuncArgs`). None of the 15 real files checked (below) use method 2,
so whether the SAVED `EyeTransform{2}` mirrors this UI-authoring-time struct
or a leaner runtime-only subset is not established either way -- this
module states only what `init_tform()` itself contains, not what ends up on
disk. Method 3 ("2-D Spatial Transformation") stores a `projective_transform`
object dominated by a function handle and fit data, likewise unverified and
essentially opaque to a reader this narrow. `Bhv2Calibration.a` is therefore
whatever plain `double` fields this module finds as DIRECT children of the
active method's struct, in field order, concatenated -- not asserted to be
six of anything, for any method. `as_affine_map` is where a six-number `a`
becomes usable and anything else is declined; see its own docstring.

**`PixelsPerDegree` is two numbers, not one.** Confirmed directly against a
real file (below): `(41.24200792470175, -41.24200792470175)`. This matches
`mlconfig.m`'s own getter, `val = [1 -1] * pixels_in_diagonal / viewing_deg`
-- equal magnitude, sign-flipped by construction, not two independent
measurements. `Bhv2Calibration.pixels_per_degree` takes element 0 (the
positive one) and discards element 1, which loses no information given the
sign relationship is fixed -- and matches NIMH ML's own convention:
`mlscreen.m` does exactly the same thing
(`obj.PixelsPerDegree = MLConfig.PixelsPerDegree(1);`).

**`EyeTransform` decode is narrowed to the selected method only.**
`EyeCalibration` (1-3) is read before `EyeTransform` is reached -- true in
the field order MonkeyLogic actually writes (`mlconfig.m`'s own property
declaration lists `EyeCalibration` before `EyeTransform`, and every one of
15 real files checked follows it) -- and only that one cell is materialised;
the other two are walked past via `_skip_value`, never decoded. This is
foremost an EFFICIENCY change, stated precisely rather than overclaimed: in
this module's own implementation, `_skip_value` is exactly as exposed as
full materialisation to a length overrun or an unrecognised type tag inside
a compound block, since neither can be skipped without being walked --
nothing in the format records a compound block's total byte size (`_walk`'s
docstring) -- so an anomalous UNSELECTED cell can still raise
`Bhv2Unreadable` either way. What narrowing buys: two-thirds of
`EyeTransform` is no longer decoded into Python objects that would be
discarded unread, and every unselected cell now goes through the one code
path (`_skip_value`) every other unwanted block in the file already uses,
rather than a second, less-exercised path (full materialisation) that used
to run for cells nobody asked for. If `EyeTransform` is somehow reached
before `EyeCalibration` is known -- an ordering never observed -- this
declines rather than guesses: no cell is selected, and `a` comes back
`None`, the same as absence.

**Real files exist; this module has been run against one.** The GitHub
mirror cited above (`Doug1983/MonkeyLogic`) ships 15 real `.bhv2` files
under `task/**/*.bhv2` -- genuine recordings, not this module's own
synthetic round-trip fixture (`tests/eye/test_bhv2.py`). None is committed
to this repository: the mirror declares no licence (`license: None` via the
GitHub API, no `LICENSE`/`COPYING`/`NOTICE` file anywhere in its tree) and
the official <https://monkeylogic.nimh.nih.gov> site states no explicit
public-domain or licensing terms either (checked `about.html` and
`download.html`, both 2026-08-30, for "licen"/"copyright"/"public
domain"/"government work" -- zero hits), so redistribution rights are
unclear; see the task-6 report for the fuller reasoning and the open
request for a ruling. The smallest of the 15
(`task/UE4_Test/171213_Me_UE_Test.bhv2`, 6,586 bytes) was fetched and run
against this module locally -- never committed, deleted after this check --
and parsed in well under 1 ms: `present=True`, `a=(0.0, 0.0)` (method 1,
`EyeTransform{1}.offset`), `pixels_per_degree=41.24200792470175` -- the
exact figures cited in the two paragraphs above.

**`mlbhv2.m`'s legacy compatibility branch is not implemented.**
`read_variable` has `if strncmp(type,'ml',2), type = 'struct'; end`,
implying an older writer tagged struct-like blocks with an `'ml'`-prefixed
class-name string instead of the literal tag `'struct'`. This module
requires `type_tag == "struct"` exactly, so a file using that legacy
convention would be misclassified as unreadable rather than as a struct --
but that failure is a caught, catchable `Bhv2Unreadable` (unrecognised type
tag), not a silent misparse. Not observed in any of the 15 real files
checked (all use the literal `'struct'` tag).
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

# See the module docstring's "Which top-level block" section for why these
# are "MLConfig" and its own fields, not the "ScreenInfo" name this task's
# brief originally used.
_TOP_LEVEL_BLOCK = "MLConfig"
_PIXELS_PER_DEGREE_FIELD = "PixelsPerDegree"
_EYE_CALIBRATION_FIELD = "EyeCalibration"
_EYE_TRANSFORM_FIELD = "EyeTransform"


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

    pixels_per_degree, a = _walk(buf)
    return Bhv2Calibration(present=a is not None, a=a, pixels_per_degree=pixels_per_degree)


def as_affine_map(cal: Bhv2Calibration) -> AffineMap | None:
    """`cal.a` as a borrowed `AffineMap`, if and only if it is exactly six
    numbers.

    MonkeyLogic's own calibration is not always six numbers (module
    docstring): Raw Signal gives two, Origin & Gain's real saved shape is
    unverified but its calibration-authoring UI alone initialises on the
    order of twenty numbers, and the 2-D Spatial Transformation method gives
    however many plain `double` fields happen to sit directly on an
    otherwise-opaque projective/polynomial object. Forcing a
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


def _harvest_doubles(materialized_struct: object) -> list[float]:
    """All plain `double` fields of a `_materialize`d struct's first (and,
    for everything this module reads, only) array element, in field order,
    concatenated. `materialized_struct` is `_materialize`'s own struct
    return shape -- a list of per-element `{field_name: value}` dicts,
    e.g. `EyeTransform`'s selected cell -- or anything else (`None`, an
    empty list, a non-struct value), which yields no doubles rather than
    raising. A `char` field or a nested struct/cell is present in the dict
    but is not itself a `tuple[float, ...]`, so it is skipped here, not
    re-descended -- this module goes no deeper than the method's own
    immediate fields.
    """
    if not (isinstance(materialized_struct, list) and materialized_struct):
        return []
    fields = materialized_struct[0]
    if not isinstance(fields, dict):
        return []
    harvested: list[float] = []
    for value in fields.values():
        if isinstance(value, tuple) and value and all(isinstance(v, float) for v in value):
            harvested.extend(value)
    return harvested


def _extract_mlconfig(
    buf: bytes, offset: int, nfield: int, n_elements: int
) -> tuple[int, float | None, tuple[float, ...] | None]:
    """One pass over `MLConfig`'s own fields, in wire order.

    `PixelsPerDegree` and `EyeCalibration` are small and always
    materialised. `EyeTransform` -- three calibration-method structs, only
    one of them active -- is handled specially: by the time it is reached,
    `EyeCalibration` has (in the field order MonkeyLogic actually writes,
    and every one of 15 real files checked -- module docstring) already
    been seen, so only the SELECTED cell (1-based, MATLAB-indexed) is
    materialised; the other two are walked past by `_skip_value`, never
    decoded (module docstring's efficiency note -- this narrows which code
    path an unselected cell's bytes go through, it does not make an
    unselected cell immune to a length overrun or an unrecognised type
    tag, since nothing can be skipped without being walked). If
    `EyeTransform` is somehow reached before `EyeCalibration` is known --
    an ordering never observed -- no cell is selected and `a` comes back
    `None`, the same as absence, rather than guessing.

    `MLConfig` is realistically always a 1x1 struct (one configuration per
    session), but `n_elements` is still honoured for any additional
    elements' own bytes to be walked correctly -- only element 0's values
    are kept, matching what a struct array walk already does everywhere
    else in this module.
    """
    pixels_per_degree: float | None = None
    a: tuple[float, ...] | None = None

    for element_index in range(n_elements):
        element_ppd: float | None = None
        method_index: int | None = None
        harvested: list[float] | None = None

        for _ in range(nfield):
            offset, name, type_tag, dims = _read_header(buf, offset)
            if name == _PIXELS_PER_DEGREE_FIELD:
                offset, value = _materialize(buf, offset, type_tag, dims)
                if isinstance(value, tuple) and value:
                    element_ppd = float(value[0])
            elif name == _EYE_CALIBRATION_FIELD:
                offset, value = _materialize(buf, offset, type_tag, dims)
                if isinstance(value, tuple) and value:
                    method_index = int(round(value[0])) - 1  # MATLAB is 1-based.
            elif name == _EYE_TRANSFORM_FIELD and type_tag == "cell":
                selected: object = None
                for cell_index in range(_prod(dims)):
                    offset, _ename, etype, edims = _read_header(buf, offset)
                    if method_index is not None and cell_index == method_index:
                        offset, selected = _materialize(buf, offset, etype, edims)
                    else:
                        offset = _skip_value(buf, offset, etype, edims)
                harvested = _harvest_doubles(selected)
            else:
                offset = _skip_value(buf, offset, type_tag, dims)

        if element_index == 0:
            pixels_per_degree = element_ppd
            a = tuple(harvested) if harvested else None

    return offset, pixels_per_degree, a


def _walk(buf: bytes) -> tuple[float | None, tuple[float, ...] | None]:
    """Every top-level block, until EOF; `MLConfig`'s `PixelsPerDegree` and
    calibration if found, else `(None, None)`.

    Every OTHER top-level block (`BehavioralCodes`, `AnalogData`, trial
    structure, and anything else) is walked structurally -- struct and cell
    blocks cannot be skipped by arithmetic alone, since nothing in the
    format records a compound block's total byte size -- but never
    materialised into Python values. That is what "skip by its declared
    length without parsing it" means here for a compound block: its bytes
    are consumed, never its meaning.
    """
    offset = 0
    n = len(buf)
    found = False
    pixels_per_degree: float | None = None
    a: tuple[float, ...] | None = None
    while offset < n:
        offset, name, type_tag, dims = _read_header(buf, offset)
        if name == _TOP_LEVEL_BLOCK and type_tag == "struct":
            offset, nfield = _read_uint64(buf, offset)
            offset, pixels_per_degree, a = _extract_mlconfig(buf, offset, nfield, _prod(dims))
            found = True
        else:
            offset = _skip_value(buf, offset, type_tag, dims)
    return (pixels_per_degree, a) if found else (None, None)


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
