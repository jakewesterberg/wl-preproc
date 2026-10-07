"""U'n'Eye's copied code and networks (design spec
`2026-10-06-uneye-design.md` section 2): upstream's files at f97ca88, but for
one recorded import line, and only its two-class networks."""

from __future__ import annotations

import fnmatch
import hashlib
import tomllib
from pathlib import Path


COPY = Path(__file__).parents[3] / "wl_preproc" / "eye" / "vendor" / "uneye"

# sha256 of each file at berenslab/uneye f97ca885dd6786cbb2ee8a564e42d781f5f453b9,
# by its path upstream and here.
UPSTREAM_SHA256 = {
    "__init__.py": "b11564899779b674d4aa7daf34e81928f58d8ca6ac07dbcdfe9a42ec168c2a0a",
    "classifier.py": "cb6f73ed5d5ccb47e44f84068bcd1dcc677684532af4925c4a940119f8e1b92f",
    "functions.py": "36026aaf2554d1c3fc3d63722009e2b3a6d909f9addaa3431ac16c1c81fb4719",
    "training/notes.md": "09053cdf845f2a11a1244b452cd9f0a4554ab1e0050393c6548f5f3f46328e85",
    "training/weights_1+2+3": "b29f3c8c594ce74d077b1469fff0b91251b61df4c5bded2723f1d155f1fc20ec",
    "training/weights_dataset1": "8e217cfb8b0d1498d43e3b8075a81087e54a5488bc4dba6d3e55bfdb652c24ef",
    "training/weights_dataset2": "cca358f760c6df4e881dca3878c7b403a246eb7d7157f59fd69a5e2214e1058d",
    "training/weights_dataset3": "d46d77451bb44371e61358644a741f0386ec910180c5da31ff81442ef87d75ac",
    "training/weights_synthetic": "e41fe1f3d8f8dabf84f00a9a3715f3b7b3eea31df4f346919e25c6d8a40598cb",
}
# The one line that differs, as PROVENANCE.md records it: upstream imports its
# package by a top-level name that does not exist inside `wl_preproc`.
CHANGED = {"classifier.py": (b"from .functions import *", b"from uneye.functions import *")}


def test_the_copy_is_upstream_at_f97ca88_but_one_import_line():
    for name, expected in UPSTREAM_SHA256.items():
        content = (COPY / name).read_bytes()
        if name in CHANGED:
            here, upstream = CHANGED[name]
            assert content.count(here) == 1, name
            content = content.replace(here, upstream)
        assert hashlib.sha256(content).hexdigest() == expected, name


def test_only_the_recorded_files_are_copied():
    """The five-class `weights_Andersson`, upstream's script, notebook and data
    are not copied (spec section 2)."""
    copied = {path.relative_to(COPY).as_posix() for path in COPY.rglob("*")
              if path.is_file() and "__pycache__" not in path.parts}
    assert copied == set(UPSTREAM_SHA256) | {"PROVENANCE.md"}


def test_each_copied_network_has_two_classes():
    """Fixation (0) and saccade (1), the only classes upstream documents."""
    import torch

    for name in UPSTREAM_SHA256:
        if name.startswith("training/weights_"):
            assert torch.load(COPY / name)["c7.weight"].shape[0] == 2, name


def test_the_networks_ship_with_the_package():
    """An installed `wl_preproc`, not only an editable one, finds them."""
    pyproject = tomllib.loads((Path(__file__).parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["wl_preproc.eye.vendor.uneye"]
    for name in UPSTREAM_SHA256:
        if not name.endswith(".py"):
            assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), name
