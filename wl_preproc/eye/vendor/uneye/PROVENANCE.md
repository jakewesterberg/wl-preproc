# U'n'Eye, copied

- **Source:** `https://github.com/berenslab/uneye`
- **Commit:** `f97ca885dd6786cbb2ee8a564e42d781f5f453b9` (2020-02-29, "contact info updated"),
  upstream's last. It has no tags or releases and is not on PyPI.
- **Fetched:** 2026-10-06.
- **Licence:** none. The repository declares no licence and its README states none. This copy
  is kept in a private repository and is never redistributed. The saccade-detection design spec
  (`docs/superpowers/specs/2026-08-31-saccade-detection-design.md` §8) rules copying it anyway:
  a trained network's weights are the method, and cannot be rewritten from a paper.
- **Cite:** Bellet, M. E., Bellet, J., Nienborg, H., Hafed, Z. M., & Berens, P. (2019).
  Human-level saccade detection performance using deep neural networks. *Journal of
  Neurophysiology*, 121(2), 646–661. doi:10.1152/jn.00601.2018

## What is copied

- **Upstream's `uneye/` package:** `__init__.py`, `classifier.py`, `functions.py`.
- **From upstream's `training/`:** `notes.md` and the five two-class networks
  (`weights_1+2+3`, `weights_dataset1`, `weights_dataset2`, `weights_dataset3`,
  `weights_synthetic`).

Not copied:
- **`weights_Andersson`.** It has five output classes, and upstream documents only fixation (0)
  and saccade (1). Its other three cannot be mapped onto this pipeline's labels honestly.
- Upstream's command-line script, notebook, analysis scripts and sample data.

## What differs from upstream

**One line.** `classifier.py`'s `from uneye.functions import *` is `from .functions import *`
here, because the package no longer sits at the top level. Nothing else is edited or
reformatted, so the copy still diffs cleanly against upstream.
`tests/eye/detect/test_uneye_copy.py` holds every copied file to upstream's sha256, with that
line reverted.

`wl_preproc/eye/detect/uneye.py` adapts the network to this pipeline (design spec
`docs/superpowers/specs/2026-10-06-uneye-design.md`).
