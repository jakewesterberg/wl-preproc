"""NWB export: one activation's file, for everything that exists before ephys.

Parent spec `2026-08-12-wl-preproc-design.md` section 8; design spec
`2026-09-28-nwb-builder-design.md`. The writers in this package take plain
data and add to an `NWBFile`; `gather.py` is the one place that reads the
database and the raw ohDPI file, and `build.py` puts the two together.
"""
