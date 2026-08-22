"""Fitting a device's clock to session time.

Two fits, at two scales, and the split is the design's central claim (spec
section 4.5):

* **Rate, once per `(system, session)`**, pooling every barcode across every one
  of that system's segments. A full session fits rate to well under 1 ppm.
* **Offset, once per `(system, segment)`**, from that segment's own barcodes,
  with the session rate held fixed.

A 3 s segment therefore never estimates its own rate. That is not a shortcut:
fitting rate locally from two barcodes spanning ~2 s yields ~16 ppm, which is
*worse* than inheriting a session-wide fit. The split also buys a free integrity
check -- a segment whose local barcodes disagree with the device-level rate
indicates a mis-assigned file or a device clock reset, and that surfaces as
`residual_us`, never as a silent millisecond error.

Pure numeric: no DataJoint, no file I/O, no `wl_preproc.synth`. It is testable
without a database, and the tables in `schema/timebase.py` store what it
returns rather than computing anything themselves.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wl_sync.barcode import Barcode

# Rate needs a slope, and a slope needs two points. One barcode establishes a
# position and nothing else.
_MIN_BARCODES_FOR_RATE = 2


@dataclass(frozen=True, slots=True)
class RateFit:
    """One system's clock against session time, over a whole session.

    `fitted_rate_hz` and `drift_ppm` are two views of one number --
    `fitted_rate_hz == nominal_rate_hz * (1 + drift_ppm / 1e6)` -- and a test
    asserts they cannot disagree. Both are kept because the tables store the
    rate and the reports read the drift.
    """

    fitted_rate_hz: float
    drift_ppm: float
    n_matched: int
    residual_us_rms: float
    residual_us_max: float

    @property
    def scale(self) -> float:
        """Device seconds per session second: `1 + drift_ppm / 1e6`.

        The quantity every conversion actually uses, named so that callers do
        not each re-derive it from `drift_ppm` and get the sign wrong once.
        """
        return 1.0 + self.drift_ppm / 1e6


@dataclass(frozen=True, slots=True)
class OffsetFit:
    """One segment's position, with the session rate held fixed.

    `offset_s` is what you ADD to rate-corrected device time to get session
    time: `session_s = device_s / rate.scale + offset_s`. The direction is
    asserted in the tests rather than left here, because a sign error is a
    session-long shift that every residual reports as zero -- the fit is
    perfectly self-consistent in the wrong frame.
    """

    offset_s: float
    residual_us: float
    n_barcodes: int


def _matched_pairs(
    device_barcodes: Sequence[Barcode], reference_s: Mapping[int, float]
) -> list[tuple[float, float]]:
    """`(session_s, device_s)` for every barcode both sides recorded.

    **Matched by value, never by ordinal position.** A barcode value is a
    32-bit monotonic counter (spec section 4.1), so it identifies itself; two
    lists paired by index instead agree perfectly on clean data and are
    catastrophically wrong on exactly the data that matters, because one
    dropped barcode shifts every later pair by one. Parent spec section 4.2
    applies the same rule to trials, for the same reason.

    A device barcode absent from the reference is dropped rather than paired
    with a neighbour: the sync box is the reference, and a barcode it did not
    record is not evidence about this device's clock.
    """
    pairs = []
    for barcode in device_barcodes:
        session_s = reference_s.get(barcode.value)
        if session_s is not None:
            pairs.append((session_s, barcode.start_us / 1e6))
    return pairs


def fit_rate(
    device_barcodes: Sequence[Barcode],
    reference_s: Mapping[int, float],
    nominal_rate_hz: float,
) -> RateFit:
    """Least squares of device time against session time, pooled over a session.

    `reference_s` maps barcode value to sync-box session time in seconds; the
    sync box defines session time, so it needs no fit of its own.

    Raises `ValueError` below two matched barcodes rather than returning the
    nominal rate and zero drift. That return would be a number which reads like
    a measurement and is not one -- the defect shape this project's checkpoint
    records repeatedly -- and the caller that must handle it is spec section
    4.1's rejection path, which needs to know.
    """
    pairs = _matched_pairs(device_barcodes, reference_s)
    if len(pairs) < _MIN_BARCODES_FOR_RATE:
        raise ValueError(
            f"{len(pairs)} barcode{'' if len(pairs) == 1 else 's'} matched the "
            f"reference, and a rate needs at least {_MIN_BARCODES_FOR_RATE}: "
            "one point establishes a position, not a slope"
        )

    n = len(pairs)
    mean_session = sum(session for session, _device in pairs) / n
    mean_device = sum(device for _session, device in pairs) / n

    # Centred least squares. Centring is not cosmetic here: session times run to
    # thousands of seconds and the slope is being resolved to parts per
    # million, so the uncentred normal equations lose the very digits the fit
    # exists to produce.
    covariance = sum(
        (session - mean_session) * (device - mean_device) for session, device in pairs
    )
    variance = sum((session - mean_session) ** 2 for session, _device in pairs)
    if variance <= 0.0:
        raise ValueError(
            f"all {n} matched barcodes share one session time, so they span no "
            "interval and no rate can be fitted from them"
        )
    scale = covariance / variance
    intercept = mean_device - scale * mean_session

    # Residuals in the SESSION frame -- the error a downstream consumer would
    # actually make -- rather than in the device frame. The two differ by the
    # scale factor, which is a part-per-million effect on the residual itself
    # and so invisible; the reason to prefer the session frame is that it is
    # the frame the number is reported in.
    residuals_us = [
        abs((device - intercept) / scale - session) * 1e6 for session, device in pairs
    ]

    return RateFit(
        fitted_rate_hz=nominal_rate_hz * scale,
        drift_ppm=(scale - 1.0) * 1e6,
        n_matched=n,
        residual_us_rms=(sum(r * r for r in residuals_us) / n) ** 0.5,
        residual_us_max=max(residuals_us),
    )


def fit_offset(
    device_barcodes: Sequence[Barcode],
    reference_s: Mapping[int, float],
    rate_fit: RateFit,
) -> OffsetFit:
    """This segment's position, with `rate_fit`'s rate held fixed.

    The rate is an argument rather than something re-estimated here, and that
    is the whole point of the split -- see the module docstring.

    Raises `ValueError` on zero matched barcodes. Zero barcodes is not an offset
    of zero: it is unalignable, and spec section 4.1 sends the file to
    `RejectedSegment` with a reason rather than letting it become a segment
    positioned at the origin.
    """
    pairs = _matched_pairs(device_barcodes, reference_s)
    if not pairs:
        raise ValueError(
            "no barcodes in this segment matched the reference, so it cannot be "
            "positioned in session time at all -- which is a rejection with a "
            "reason, not an offset of zero"
        )

    scale = rate_fit.scale
    errors = [session - device / scale for session, device in pairs]
    offset_s = sum(errors) / len(errors)
    residuals_us = [abs(error - offset_s) * 1e6 for error in errors]

    return OffsetFit(
        offset_s=offset_s,
        residual_us=(sum(r * r for r in residuals_us) / len(residuals_us)) ** 0.5,
        n_barcodes=len(pairs),
    )
