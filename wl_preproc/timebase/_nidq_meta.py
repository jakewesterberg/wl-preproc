"""The SpikeGLX `.meta` sidecar's one reader, for the fields extraction needs.

A `.meta` is `key=value` lines, plus `~`-prefixed keys whose values are
parenthesised tuple lists. Two fields matter to extraction -- the sampling rate
and the channel-type census -- and both are load-bearing:

* the **rate** converts native samples into native microseconds, and a rate
  assumed rather than read is a fit wrong by exactly the ratio nobody checked;
* the **census** (`snsMnMaXaDw`) says how many channels of each kind the
  interleaved binary holds, and the digital word is the last of them. Reshaping
  by a guessed channel count does not fail -- it silently reads a strided
  mixture of analog channels as if it were a digital line.

A third, `fileCreateTime`, places the NI's first sample on the wall clock when
the NI keeps a session's time (design spec
`2026-10-18-training-records-and-backup-clock-design.md`, amendment 46). It is
optional: extraction never needs it.

`spikeinterface`/`neo` parses all of this properly and the synth tests use it
as the format oracle, but it is a **dev-only** dependency (`pyproject.toml`),
so nothing under `wl_preproc/` may import it.

Kept in its own module, separate from `extract.py`, so the sidecar's shape has
exactly one reader -- the same reason `_syncbox_log.py` and `_rhs_header.py`
exist.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

_SAMPLE_RATE_KEY = "niSampRate"
_CENSUS_KEY = "snsMnMaXaDw"
_CREATED_KEY = "fileCreateTime"


@dataclass(frozen=True, slots=True)
class NidqMeta:
    """What a `.nidq.meta` says about the shape of its `.bin`.

    `n_digital_words` is almost always 1 -- one int16 per sample carrying all
    16 Port 0 lines -- but it is read rather than assumed, because a second
    word is how a 32-line port is saved and that is exactly the card spec
    section 12 orders.

    `file_created` is SpikeGLX's `fileCreateTime`, "the local date and time
    on the recording machine" to the second, naive because SpikeGLX writes no
    zone; it marks the file's first sample. None where the `.meta` has none,
    or one that is not a naive ISO 8601 time.
    """

    sample_rate_hz: float
    n_analog_channels: int
    n_digital_words: int
    file_created: datetime.datetime | None = None

    @property
    def n_channels(self) -> int:
        return self.n_analog_channels + self.n_digital_words


def _parse(path: Path) -> dict[str, str]:
    """`key=value` lines into a dict, keeping `~` keys verbatim.

    A line without exactly one `=` is skipped rather than raising: the `~`
    values contain no `=`, but SpikeGLX has added fields between versions and a
    reader that dies on an unfamiliar line is a reader that dies on next
    year's SpikeGLX. The fields this module needs are checked for explicitly
    below, so a genuinely unusable sidecar still fails -- by name.
    """
    fields: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and "=" not in value:
            fields[key] = value
    return fields


def read_nidq_meta(path: Path) -> NidqMeta:
    """The rate and channel census out of a `.nidq.meta`."""
    fields = _parse(path)

    missing = [key for key in (_SAMPLE_RATE_KEY, _CENSUS_KEY) if key not in fields]
    if missing:
        raise ValueError(
            f"{path}: no {' or '.join(missing)}; this is not a SpikeGLX NI "
            f"sidecar (found {len(fields)} fields)"
        )

    try:
        sample_rate_hz = float(fields[_SAMPLE_RATE_KEY])
    except ValueError as exc:
        raise ValueError(
            f"{path}: {_SAMPLE_RATE_KEY}={fields[_SAMPLE_RATE_KEY]!r} is not a number"
        ) from exc
    if not sample_rate_hz > 0.0:
        raise ValueError(
            f"{path}: declares {sample_rate_hz} Hz, which cannot time anything"
        )

    # Multiplexed neural, multiplexed aux, non-muxed analog, digital words --
    # in that order, which is also their order within each interleaved sample.
    try:
        mn, ma, xa, dw = (int(part) for part in fields[_CENSUS_KEY].split(","))
    except ValueError as exc:
        raise ValueError(
            f"{path}: {_CENSUS_KEY}={fields[_CENSUS_KEY]!r} is not the four "
            "comma-separated counts MN,MA,XA,DW"
        ) from exc
    if dw < 1:
        raise ValueError(
            f"{path}: {_CENSUS_KEY} declares {dw} digital word channels, so "
            "this file carries no digital lines and cannot hold a barcode"
        )

    return NidqMeta(
        sample_rate_hz=sample_rate_hz,
        n_analog_channels=mn + ma + xa,
        n_digital_words=dw,
        file_created=_file_created(fields.get(_CREATED_KEY)),
    )


def _file_created(text: str | None) -> datetime.datetime | None:
    """`fileCreateTime`, read but never required: a sidecar whose creation
    time is missing or unreadable still times its samples, and only the wall
    clock's second source under the NI is lost."""
    if text is None:
        return None
    try:
        created = datetime.datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    return created if created.tzinfo is None else None
