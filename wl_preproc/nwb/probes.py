"""Every probe, its electrodes and its areas (design spec
`2026-09-30-nwb-probes-design.md` section 3.1).

**The recording says which probe was used; wl.works says where it went** (the
requester's decision 2 of 2026-09-30). A device is named for the serial the
recording's `.meta` names, and an electrode group for the insertion wl.works
reported. Every electrode carries its insertion's area (decision 3): the aim
and the latest assignment, each labelled, per insertion and not
depth-resolved. pynwb fixes the electrode table's own description (NWB's
`ElectrodesTable`, measured on 4.1), so each area column says so instead."""

from __future__ import annotations

from pynwb import NWBFile

# How a group's description names an assignment's source (wl.works' Plan 19
# list). The design spec's own example reads "assigned at rig: V4d".
_ASSIGNED_BY = {
    "histology": "by histology",
    "functional_mapping": "by functional mapping",
    "waveform_depth": "by waveform depth",
    "structural_imaging": "by structural imaging",
    "at_rig": "at rig",
    "other": "otherwise",
}

_COLUMNS = (
    ("shank", "The site's shank on its probe."),
    ("electrode", "The site's number on its probe model, as ephys.ProbeType.Electrode numbers it."),
    ("target_area", "The insertion's aim, as wl.works' item_insertion.targetArea states it, or empty when none "
                    "was reported. The insertion's, on every one of its electrodes: not depth-resolved."),
    ("assigned_area", "The latest area wl.works had assigned the insertion when this file was built, or empty. "
                      "The insertion's, not depth-resolved; a later assignment does not rewrite this file."),
    ("assigned_area_source", "Who or what assigned it: histology, functional_mapping, waveform_depth, "
                             "structural_imaging, at_rig or other; empty when nothing was assigned."),
)


def area_text(probe: dict) -> str:
    """Both areas, each labelled, for a group's description:
    `target: V4d (CHARM, level 6); assigned at rig: V4d (2027-01-12)`."""
    parts = []
    if probe["target"] is not None:
        target = probe["target"]
        parts.append(f"target: {target['area']} ({target['atlas']}, level {target['atlas_level']})")
    if probe["assignment"] is not None:
        assignment = probe["assignment"]
        parts.append(f"assigned {_ASSIGNED_BY[assignment['source']]}: {assignment['area']} "
                     f"({assignment['asserted_at']:%Y-%m-%d})")
    return ("; ".join(parts) or "no area reported") + ". The insertion's area, not depth-resolved."


def add_probes(nwb: NWBFile, probes: list[dict]) -> None:
    """A device per probe, named `probe-<serial>` and carrying its part
    number as its model; an electrode group per insertion, or per probe when
    wl.works reported none, located at the area label; and one electrode row
    per active site. `probes` is `gather.Gathered.probes`."""
    models, groups = {}, []
    for probe in probes:
        part = probe["probe_type"]
        if part is not None and part not in models:
            models[part] = nwb.create_device_model(name=part, manufacturer="IMEC", model_number=part,
                                                   description=f"Neuropixels probe model {part}.")
        device = nwb.create_device(
            name=f"probe-{probe['serial']}", serial_number=probe["serial"], model=models.get(part),
            description=(f"The probe the recording's .meta names, serial {probe['serial']}." if part is not None
                         else f"Probe {probe['serial']}, as wl.works' report names it; the recording does not "
                              "name its type."))
        number = probe["insertion_number"]
        groups.append((probe, nwb.create_electrode_group(
            name=f"insertion-{number}" if number is not None else f"probe-{probe['serial']}",
            description=area_text(probe), location=probe["area"], device=device)))
    if not any(probe["electrodes"] for probe in probes):
        return
    for name, description in _COLUMNS:
        nwb.add_electrode_column(name=name, description=description)
    for probe, group in groups:
        target, assignment = probe["target"] or {}, probe["assignment"] or {}
        for site in probe["electrodes"]:
            nwb.add_electrode(group=group, location=probe["area"], rel_x=site["x"], rel_y=site["y"],
                              shank=site["shank"], electrode=site["electrode"], target_area=target.get("area", ""),
                              assigned_area=assignment.get("area", ""),
                              assigned_area_source=assignment.get("source", ""))
