"""The Andersson dataset's own loader and file list, shared by every
validation test that reads it (spec 5.3).

Both are extracted verbatim from `test_remodnav_validation.py`, which in
turn followed `paper-remodnav`'s `mk_figuresnstats.py` (CC-BY-4.0): its
`labeled_files` dict (here `ANDERSSON_FILES`) and its `load_anderson`
function (here `load_andersson`) -- the `.mat` layout (`ETdata`'s
`viewDist`, `screenDim`, `screenRes`, `sampFreq`, `pos`), the
`px2deg` formula, and dichotomising `(0, 0)` positions to `NaN`.

**The Andersson dataset is GPL-3.0 and is never committed.** Nothing from
`github.com/richardandersson/EyeMovementDetectorEvaluation` is vendored
here; this module only knows the on-disk layout of a local clone.

Imports nothing from `wl_preproc.schema` (this file lives in `tests/eye/`).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

#: `mk_figuresnstats.py`'s own `labeled_files`, verbatim.
ANDERSSON_FILES = {
    "dots": [
        "TH20_trial1_labelled_{}.mat", "TH38_trial1_labelled_{}.mat", "TL22_trial17_labelled_{}.mat",
        "TL24_trial17_labelled_{}.mat", "UH21_trial17_labelled_{}.mat", "UH21_trial1_labelled_{}.mat",
        "UH25_trial1_labelled_{}.mat", "UH33_trial17_labelled_{}.mat", "UL27_trial17_labelled_{}.mat",
        "UL31_trial1_labelled_{}.mat", "UL39_trial1_labelled_{}.mat",
    ],
    "img": [
        "TH34_img_Europe_labelled_{}.mat", "TH34_img_vy_labelled_{}.mat", "TL20_img_konijntjes_labelled_{}.mat",
        "TL28_img_konijntjes_labelled_{}.mat", "UH21_img_Rome_labelled_{}.mat", "UH27_img_vy_labelled_{}.mat",
        "UH29_img_Europe_labelled_{}.mat", "UH33_img_vy_labelled_{}.mat", "UH47_img_Europe_labelled_{}.mat",
        "UL23_img_Europe_labelled_{}.mat", "UL31_img_konijntjes_labelled_{}.mat",
        "UL39_img_konijntjes_labelled_{}.mat", "UL43_img_Rome_labelled_{}.mat",
        "UL47_img_konijntjes_labelled_{}.mat",
    ],
    "video": [
        "TH34_video_BergoDalbana_labelled_{}.mat", "TH38_video_dolphin_fov_labelled_{}.mat",
        "TL30_video_triple_jump_labelled_{}.mat", "UH21_video_BergoDalbana_labelled_{}.mat",
        "UH29_video_dolphin_fov_labelled_{}.mat", "UH47_video_BergoDalbana_labelled_{}.mat",
        "UL23_video_triple_jump_labelled_{}.mat", "UL27_video_triple_jump_labelled_{}.mat",
        "UL31_video_triple_jump_labelled_{}.mat",
    ],
}


def load_andersson(root: Path, stim: str, name: str):
    from scipy.io import loadmat

    et = loadmat(root / "annotated_data" / "data used in the article" / stim / name)["ETdata"]
    view_dist = float(et["viewDist"][0][0][0][0])
    screen_width = float(et["screenDim"][0][0][0][0])
    screen_res = float(et["screenRes"][0][0][0][0])
    px2deg = math.degrees(math.atan2(0.5 * screen_width, view_dist)) / (0.5 * screen_res)
    fs = float(et["sampFreq"][0][0][0][0])
    pos = et["pos"][0][0]
    x, y = pos[:, 3].astype(float), pos[:, 4].astype(float)
    missing = (x == 0) & (y == 0)
    x[missing] = np.nan
    y[missing] = np.nan
    return np.column_stack([x, y]), pos[:, 5], px2deg, fs
