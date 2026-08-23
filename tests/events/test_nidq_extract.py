"""NI word extraction: latched at the strobe's FAR edge (spec section 4.2.1)."""

from __future__ import annotations


def test_nidq_latches_the_word_at_the_strobes_FAR_edge(tmp_path):
    """Spec section 4.2.1: "T1 is the strobe pulse width, and the latching edge
    is its far end. Data has therefore been stable for T1 when the receiver
    latches."

    Sampling at the RISING edge would still pass on a fixture where data and
    strobe assert together -- which they do. So this test makes the data CHANGE
    mid-pulse and asserts the far-edge value wins. Sampling the near edge
    returns the stale word and fails.
    """
    import numpy as np

    from wl_preproc.events import extract

    fs = 25_000.0
    n = 1_000
    control = np.zeros(n, dtype=np.uint16)   # word 0: barcode, strobe
    data = np.zeros(n, dtype=np.uint16)      # word 1: the 16 data lines

    # One strobe from sample 100 to 120. Data is 0x00AA for the first half and
    # 0x00BB for the second; only the latter is latched.
    control[100:120] |= 1 << 1
    data[100:110] = 0x00AA
    data[110:120] = 0x00BB

    interleaved = np.empty(n * 2, dtype=np.int16)
    interleaved[0::2] = control.astype(np.int16)
    interleaved[1::2] = data.astype(np.int16)
    path = tmp_path / "s_t0.nidq.bin"
    interleaved.tofile(path)
    (tmp_path / "s_t0.nidq.meta").write_text(
        f"niSampRate={fs}\nnSavedChans=2\nsnsMnMaXaDw=0,0,0,2\n"
        "niXDChans1=0:1\n~snsChanMap=(0,0,0,2,0)(XD0;0:1)(XD1;0:15)\n"
    )

    stream = extract.extract_nidq_words(path)
    assert len(stream.words) == 1
    _sample, word = stream.words[0]
    assert word == 0x00BB, (
        f"got 0x{word:04X}; 0x00AA means the near edge was sampled, and spec "
        "section 4.2.1 puts the latch at the far end of T1"
    )
