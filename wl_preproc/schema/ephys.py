# wl_preproc/schema/ephys.py
"""The ephys branch: probe, insertion, clustering, units, waveforms, QC.

Custom rather than adopted. `element-array-ephys` was declined 2026-08-22 --
its `Clustering` is keyed (subject, session_datetime, insertion_number,
paramset_idx) with nowhere to put `activation_id`, which parent spec section
5.2 requires, so two derivative activations over different block sets would
collide on one primary key. Adopting it also imports four unpinned moving git
refs and silently replaces this project's pinned spikeinterface. See
`docs/superpowers/specs/2026-08-22-phase-2a-ephys-schema-design.md` section 2.

**Every array attribute here declares `<blob>`.** Under DataJoint 2.x a bare
`longblob` stores a numpy array as its string repr and nothing raises on insert
or on fetch -- measured at 31,488 float32 values becoming 488 bytes.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.ephys import geometry
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline, request

schema = dj.Schema()


@schema
class ProbeType(dj.Lookup):
    definition = """
    # One probe model, named by its IMEC part number. Key: (probe_type).
    # Populated from probeinterface's OFFLINE table -- see wl_preproc/ephys/
    # geometry.py for why that source and not element-array-ephys's map.
    probe_type : varchar(32)  # e.g. NP1000, NP1030
    """

    class Electrode(dj.Part):
        definition = """
        # One electrode site on a probe model, in the model's own frame.
        # Key: (probe_type, electrode).
        -> master
        electrode : int unsigned
        ---
        shank      : tinyint unsigned
        shank_col  : tinyint unsigned
        shank_row  : int unsigned
        x_coord    : float  # (um)
        y_coord    : float  # (um)
        """


@schema
class Probe(dj.Manual):
    definition = """
    # One physical probe, by serial. Key: (probe_serial). Serials arrive with
    # the activation request (parent spec section 11.2); this machine cannot
    # fetch them from wl.works.
    probe_serial : varchar(32)
    ---
    -> ProbeType
    """


@schema
class ElectrodeConfig(dj.Manual):
    definition = """
    # A SET OF ELECTRODES, named by its contents -- not 'the configuration of a
    # recording'. Key: (electrode_config_hash). That distinction is load-
    # bearing: the intersection of two electrode sets is itself an electrode
    # set, so a cross-montage derivative's effective config is a row in this
    # table like any other, and the canonical and derivative cases need no
    # branch. Design spec section 3.2.2.
    electrode_config_hash : varchar(32)
    ---
    -> ProbeType
    n_electrodes : int unsigned
    """

    class Electrode(dj.Part):
        definition = """
        # Key: (electrode_config_hash, probe_type, electrode).
        -> master
        -> ProbeType.Electrode
        """


@schema
class ProbeInsertion(dj.Manual):
    definition = """
    # One penetration in one session. Key: (subject, session_datetime,
    # insertion_number) -- exactly parent spec section 5.2's.
    -> pipeline.Session
    insertion_number : tinyint unsigned
    ---
    -> Probe
    # A SOFT reference into wl.works' `trajectory` table, not a foreign key:
    # that database is unreachable from this machine (parent spec section
    # 11.2 -- "no route in"), so the value arrives with the activation
    # request. Below the divider deliberately, the same way request.py's
    # Activation projects Request rather than inheriting its key: a
    # trajectory is a resource that outlives every session and is not
    # primary-key material here.
    #
    # It names WHICHEVER trajectory the penetration actually ran against, and
    # the planned/achieved stance is read through the reference. A penetration
    # made before any post-operative scan legitimately names a `planned` one;
    # null means only "not recorded". See wl-works
    # 2026-08-22-trajectory-identity-design.md section 4, and this phase's
    # design spec section 5.2.
    trajectory_id = null : varchar(64)
    works_insertion_id = null : varchar(64)
    """


@schema
class InsertionLocation(dj.Manual):
    definition = """
    # The AIM, carried in from wl.works' item_insertion.targetArea and its
    # atlas qualification. Key: (subject, session_datetime, insertion_number).
    #
    # Recorded, never derived. Row 27 of wl.works pins targetArea to mean the
    # aim; what was actually hit is an insertion_area_assignment there, and
    # per-electrode anatomy is authored into the NWB electrode table here --
    # see design spec section 5.4 for the three prohibitions that meet at this
    # table.
    -> ProbeInsertion
    ---
    area        : varchar(32)
    atlas       : varchar(32)
    atlas_level : tinyint unsigned
    """


@schema
class SegmentConfig(dj.Manual):
    definition = """
    # Which electrode set one probe was recording through, for one segment.
    # Key: (subject, session_datetime, insertion_number, system,
    # segment_barcode).
    #
    # Keyed on the SEGMENT, not the insertion and not the block. Bank
    # selection changes between blocks, but a SpikeGLX .meta carries exactly
    # one ~imroTbl and probeinterface returns one probe per file -- so a bank
    # change REQUIRES a restart and is already a segment boundary (parent spec
    # section 5.2.1). Blocks and segments do not align and neither is derivable
    # from the other, so the block grain would be the wrong home even though
    # the behaviour is block-aligned. Design spec section 3.2.1.
    -> ProbeInsertion
    -> core.Segment
    ---
    -> ElectrodeConfig
    """


@schema
class ClusterQualityLabel(dj.Lookup):
    definition = """
    # Key: (cluster_quality_label).
    cluster_quality_label : varchar(16)
    ---
    label_description : varchar(255)
    """
    contents = [
        ("good", "single unit"),
        ("mua", "multi-unit activity"),
        ("noise", "artifact or noise cluster"),
    ]


@schema
class Clustering(dj.Manual):
    definition = """
    # One sort. Key: (subject, session_datetime, insertion_number, montage_id,
    # activation_id, paramset_type, paramset_idx).
    #
    # Keyed on the ACTIVATION, not the session: a sort's unit identity is a
    # product of its block set (parent spec section 8.3), so two activations
    # over different block sets produce genuinely different units and nothing
    # may imply otherwise (parent spec section 5.2). montage_id arrives through
    # Activation and is stricter than section 5.2's tree as drawn, which is
    # correct -- the montage is the grain at which unit identity holds.
    #
    # paramset_type is in the key because ParamSet is keyed
    # (paramset_type, paramset_idx); it is always 'clustering' here.
    -> ProbeInsertion
    -> request.Activation
    -> paramset.ParamSet
    ---
    # The EFFECTIVE electrode set this sort ran on. For a canonical activation
    # it is the one config its segments share; for a derivative deliberately
    # spanning montages it is their INTERSECTION -- which is an ElectrodeConfig
    # row like any other, so there is no branch here. Every
    # `-> ElectrodeConfig.Electrode` below resolves against this one, which is
    # what makes Unit's peak electrode well-defined for a derivative too.
    # Design spec section 3.2.2.
    -> ElectrodeConfig
    """


@schema
class Curation(dj.Manual):
    definition = """
    # One curation pass over a sort. Key: (..., curation_id).
    -> Clustering
    curation_id : tinyint unsigned
    """


@schema
class Unit(dj.Manual):
    definition = """
    # One unit. Key: (..., curation_id, unit).
    #
    # spike_times is `<blob>` and is one of the fourteen attributes that made
    # upstream unusable. Spike times are stored at NATIVE precision as event
    # times, never decimated to the 500 Hz continuous rate -- parent spec
    # section 8.1.1: "anything whose value is its timing is stored as event
    # times at native precision".
    -> Curation
    unit : int unsigned
    ---
    -> ElectrodeConfig.Electrode
    -> ClusterQualityLabel
    spike_count : int unsigned
    spike_times : <blob>    # (s) session time
    spike_sites : <blob>    # electrode of each spike
    spike_depths = null : <blob>  # (um) depth of each spike
    """


@schema
class WaveformSet(dj.Manual):
    definition = """
    # Waveforms for one curation. Key: (..., curation_id).
    -> Curation
    """

    class PeakWaveform(dj.Part):
        definition = """
        # Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        peak_electrode_waveform : <blob>  # (uV)
        """

    class Waveform(dj.Part):
        definition = """
        # Key: (..., curation_id, unit, electrode_config_hash, probe_type,
        # electrode).
        -> master
        -> Unit
        -> ElectrodeConfig.Electrode
        ---
        waveform_mean : <blob>        # (uV) mean across spikes
        waveforms = null : <blob>     # (uV) (spike x sample), populated on request
        """


@schema
class QualityMetrics(dj.Manual):
    definition = """
    # Key: (..., curation_id).
    -> Curation
    """

    class Cluster(dj.Part):
        definition = """
        # Per-unit cluster metrics. Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        firing_rate      : float
        snr              : float
        presence_ratio   : float
        isi_violation    : float
        amplitude_cutoff : float
        """

    class Waveform(dj.Part):
        definition = """
        # Per-unit waveform metrics. Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        amplitude       : float
        duration        : float
        halfwidth       : float
        repolarisation_slope : float
        """


def register_probe_type(part_number: str) -> None:
    """Declare `part_number` and every electrode of it. Idempotent."""
    ProbeType.insert1({"probe_type": part_number}, skip_duplicates=True)
    rows = [
        {"probe_type": part_number, **row} for row in geometry.electrode_rows(part_number)
    ]
    ProbeType.Electrode.insert(rows, skip_duplicates=True)


def register_electrode_config(part_number: str, electrodes: list[int]) -> str:
    """Register the electrode set and return its hash. Idempotent.

    The hash is over the SORTED set, so an intersection computed in any order
    resolves to one identity. `paramset.content_hash` is reused by name rather
    than reimplemented -- that function's own docstring rules it, for the
    one-definition reason.
    """
    unique = sorted(set(int(e) for e in electrodes))
    config_hash = paramset.content_hash({"probe_type": part_number, "electrodes": unique})
    ElectrodeConfig.insert1(
        {
            "electrode_config_hash": config_hash,
            "probe_type": part_number,
            "n_electrodes": len(unique),
        },
        skip_duplicates=True,
    )
    ElectrodeConfig.Electrode.insert(
        [
            {
                "electrode_config_hash": config_hash,
                "probe_type": part_number,
                "electrode": e,
            }
            for e in unique
        ],
        skip_duplicates=True,
    )
    return config_hash


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}ephys`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}ephys", create_tables=True)
