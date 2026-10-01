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
    #
    # *True when written; since the probes design (2026-09-30) a row is
    # written by the probes stage, ProbeCensus, from the serial and part
    # number the recording's own .meta names -- the requester's decision that
    # the recording says which probe was used. The request's serial is
    # recorded as InsertionReport and joined to this one.*
    probe_serial : varchar(32)
    ---
    -> ProbeType
    """


@schema
class ElectrodeConfig(dj.Manual):
    definition = """
    # A SET OF ELECTRODES, named by its contents -- not 'the configuration of a
    # recording'. Key: (electrode_config_hash, probe_type). That distinction is
    # load-bearing: the intersection of two electrode sets is itself an
    # electrode set, so a cross-montage derivative's effective config is a row
    # in this table like any other, and the canonical and derivative cases
    # need no branch. Design spec section 3.2.2.
    #
    # `-> ProbeType` moved above this divider in fix round 2 (was a secondary
    # attribute): `ElectrodeConfig.Electrode` already put `probe_type` into
    # ITS OWN key via `-> ProbeType.Electrode`, but with probe_type secondary
    # here, no foreign key tied the two together -- a part row could name a
    # different probe model than its own master declared, and nothing would
    # catch it. Moving it into this table's key makes `ElectrodeConfig.
    # Electrode`'s existing `-> master` reference a genuine composite foreign
    # key on (electrode_config_hash, probe_type), which now enforces the
    # match. This adds no new row-identity ambiguity: `probe_type` is already
    # one of the values hashed into `electrode_config_hash` itself (see
    # `register_electrode_config`), so one hash still names exactly one probe
    # type -- this only makes DataJoint enforce what was already true by
    # construction. Verified this changes the primary key of no table that
    # references `ElectrodeConfig` (SegmentConfig, Clustering -- both
    # reference it below their own divider) or `ElectrodeConfig.Electrode`
    # (Unit -- also below its divider; WaveformSet.Waveform -- already carried
    # probe_type via this same diamond, so its key's shape is unchanged).
    electrode_config_hash : varchar(32)
    -> ProbeType
    ---
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
    # the planned/achieved stance is read through the reference. See wl-works
    # 2026-08-22-trajectory-identity-design.md section 4, and this phase's
    # design spec section 5.2.
    #
    # **Two clauses here were false and are corrected rather than quietly
    # edited.** They read: "A penetration made before any post-operative scan
    # legitimately names a `planned` one; null means only 'not recorded'."
    # Ruled 2026-08-26: probes are sometimes inserted along a trajectory that
    # was never planned. Such a penetration has no planned trajectory to name --
    # none was ever designed -- and no achieved one either until a
    # post-operative scan mints it, so there are cases with nothing to put here
    # at all. Null therefore does NOT mean only "not recorded"; it also covers a
    # penetration for which no trajectory resource exists.
    #
    # This host does not distinguish them and must not try. Null means "no
    # trajectory arrived with the request" and nothing further -- the same
    # discipline as `core.Block`'s "recording an assertion is not authoring it".
    # wl-works' section 9 item 1 leaves the discrimination open on their side
    # and warns against "a null that means three things"; their item 2 is why
    # the no-planned-parent case is legitimate rather than an error.
    #
    # **Not a quarantine condition.** Design spec section 8.3's "no insertion
    # record -> no canonical" is about a missing INSERTION, which hides a probe
    # move and would have the sort run straight across it. An insertion naming
    # no trajectory hides nothing: only the electrode -> CT/MR chain is
    # unavailable for that penetration.
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
class InsertionReport(dj.Manual):
    definition = """
    # What wl.works says about one insertion, as the latest job request for
    # the session said it (design spec 2026-09-30-nwb-probes-design.md section
    # 2.2). Key: (subject, session_datetime, insertion_number).
    #
    # Recorded, never derived, and not a ProbeInsertion: the serial is plain
    # text because the probe may not have been recorded yet, and the probes
    # stage links the two once it has (section 2.3). The latest request wins,
    # as with the subject's details -- the ELN is the authority -- and an
    # insertion a later request does not mention is left alone, since a
    # request may name only its own montage's insertions.
    -> pipeline.Session
    insertion_number : tinyint unsigned
    ---
    probe_serial : varchar(32)
    trajectory_id = null : varchar(64)
    target_area = null : varchar(32)
    target_atlas = null : varchar(32)
    target_atlas_level = null : tinyint unsigned
    """


@schema
class AreaAssignment(dj.Manual):
    definition = """
    # Each area assignment wl.works has reported for an insertion, append-only
    # as its own insertion_area_assignment is (section 2.2). Key: (subject,
    # session_datetime, insertion_number, asserted_at). The latest by
    # asserted_at is the insertion's assigned area.
    #
    # No foreign key to InsertionReport: that row is replaced when a later
    # request corrects it, and a replacement must not have to remove what was
    # asserted. datetime(6), so two assignments a second apart stay two.
    -> pipeline.Session
    insertion_number : tinyint unsigned
    asserted_at : datetime(6)  # naive UTC
    ---
    area : varchar(32)
    source : enum('histology','functional_mapping','waveform_depth','structural_imaging','at_rig','other')
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


# `ProbeCensus.Probe.imro_table`'s width. A Neuropixels imroTbl names one
# entry per channel, about 8,000 characters for 384 channels.
IMRO_TABLE_MAX = 10240


def kept_imro_table(table: str | None) -> tuple[str | None, str | None]:
    """The table to keep, and a problem when it is too long to keep: a value
    longer than its column would fail the segment on every pass."""
    if table is not None and len(table) > IMRO_TABLE_MAX:
        return None, f"the imroTbl is {len(table)} characters, longer than the {IMRO_TABLE_MAX} kept, so it is not kept"
    return table, None


@schema
class ProbeCensus(dj.Computed):
    definition = """
    # Which probes one SpikeGLX segment's run recorded, read from the run's own
    # .meta files (design spec 2026-09-30-nwb-probes-design.md section 2.1).
    # Key: (subject, session_datetime, system, segment_barcode).
    #
    # The row marks the segment READ -- with no probe as readily as with two.
    # The NWB builder waits on it (section 3.3), so a run that recorded no
    # probe must still have one, and the count is the master's, not a count
    # of parts that might be absent for either reason.
    -> core.Segment
    ---
    n_probes : tinyint unsigned
    """

    class Probe(dj.Part):
        definition = """
        # One imec stream of the run, in the recording's own words. Key:
        # (..., segment_barcode, stream).
        #
        # `part_number` is what the .meta says, kept whether or not
        # probeinterface knows it; the config -- and with it `probe_type` --
        # is set only when the sites could be placed, which is Phase 2a's
        # invariant: no ProbeType without its electrodes. `problem` says what
        # is missing and why, empty when nothing is.
        -> master
        stream : varchar(8)  # imec0, imec1, ...
        ---
        probe_serial = null : varchar(32)
        part_number = null : varchar(32)
        -> [nullable] ElectrodeConfig
        problem = '' : varchar(1024)
        # The segment's `~imroTbl`, verbatim from its `.meta` (design spec
        # 2026-10-01-session-listing-and-run-requests-design.md section 2.2):
        # wl.works compares a planned IMRO file with it. Null when absent, or
        # longer than the column, which is then a problem.
        imro_table = null : varchar(10240)
        """

    @property
    def key_source(self):
        return core.Segment & {"system": "spikeglx"}

    def make(self, key: dict) -> None:
        """Read the run once. A landed file does not change, so what could not
        be read is recorded as a problem rather than raised, which would fail
        the segment on every pass and record nothing."""
        from pathlib import Path

        from wl_preproc.ephys.spikeglx_probes import read_run
        from wl_preproc.schema import ingest

        session_key = {k: key[k] for k in pipeline.Session.primary_key}
        session_dir = Path((ingest.Ingestion & session_key).fetch1("session_dir"))
        run = session_dir / key["system"] / (core.Segment & key).fetch1("file_path")
        parts = []
        for probe in read_run(run):
            problems = [probe.problem] if probe.problem else []
            # Every key on every row, placed or not: DataJoint refuses a batch
            # whose rows name different fields.
            config = {"electrode_config_hash": None, "probe_type": None}
            registered = (Probe & {"probe_serial": probe.serial}).to_arrays("probe_type") if probe.serial else []
            if len(registered) and registered[0] != probe.part_number:
                # One serial is one physical probe; its type cannot change.
                problems.append(f"serial {probe.serial} is registered as {registered[0]}, but this .meta says "
                                f"{probe.part_number}; its sites are not recorded under either")
            elif probe.electrodes is not None:
                register_probe_type(probe.part_number)
                if probe.serial:
                    Probe.insert1({"probe_serial": probe.serial, "probe_type": probe.part_number},
                                  skip_duplicates=True)
                config = {
                    "electrode_config_hash": register_electrode_config(probe.part_number, list(probe.electrodes)),
                    "probe_type": probe.part_number,
                }
            imro_table, too_long = kept_imro_table(probe.imro_table)
            problems += [too_long] if too_long else []
            parts.append({**key, "stream": probe.stream, "probe_serial": probe.serial,
                          "part_number": probe.part_number, **config, "problem": "; ".join(problems),
                          "imro_table": imro_table})
        self.insert1({**key, "n_probes": len(parts)})
        self.Probe.insert(parts)


@schema
class ClusterQualityLabel(dj.Lookup):
    definition = """
    # Key: (cluster_quality_label).
    cluster_quality_label : varchar(16)
    ---
    label_description : varchar(255)
    """
    contents = [
        {"cluster_quality_label": "good", "label_description": "single unit"},
        {"cluster_quality_label": "mua", "label_description": "multi-unit activity"},
        {"cluster_quality_label": "noise", "label_description": "artifact or noise cluster"},
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
    # `-> Clustering.Electrode` below resolves against this one, which is what
    # makes Unit's peak electrode well-defined for a derivative too.
    # Design spec section 3.2.2.
    -> ElectrodeConfig
    """

    class Electrode(dj.Part):
        definition = """
        # The electrodes this sort actually ran on. Key: (..., paramset_idx,
        # electrode_config_hash, probe_type, electrode).
        #
        # This part exists to make section 3.2.2's claim a CONSTRAINT rather
        # than a convention. Before it, Unit and WaveformSet.Waveform each
        # carried their own electrode_config_hash with no foreign key back to
        # the sort, so a Unit could name an electrode from a configuration its
        # own Clustering never ran on -- and nothing would report it. They now
        # reference this part instead, so an electrode a unit names must be one
        # of the electrodes its own sort declared.
        #
        # NOT ENFORCED HERE, in the same sense ActivationBlock records: the
        # master's `-> ElectrodeConfig` is BELOW its divider, so a part row
        # cannot inherit it into this key, and nothing at the database level
        # ties these rows to that configuration. Whatever populates this table
        # must fill it from the master's own config. The alternative -- lifting
        # the hash into Clustering's primary key -- was refused: the config is
        # a function of the activation, not identity-forming, and two sorts
        # differing only by configuration would be two rows that should be one.
        -> master
        -> ElectrodeConfig.Electrode
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
    # The peak electrode, scoped to this sort's OWN electrode set rather than
    # to any ElectrodeConfig -- see Clustering.Electrode for why.
    -> Clustering.Electrode
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
        -> Clustering.Electrode
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

    class Channel(dj.Part):
        definition = """
        # Per-CHANNEL quality, parent spec section 6.6. Key: (..., curation_id,
        # electrode_config_hash, probe_type, electrode).
        #
        # Per-channel rather than per-unit, which is the whole point: a dead or
        # saturating electrode is a fact about the probe, not about any unit
        # that happened to be near it, and the per-unit parts above cannot say
        # it. Section 3.3 of the Phase 2a design named these quantities as
        # landing here; they did not, and this closes that gap.
        #
        # 50 Hz rather than 60: KU Leuven is on EU mains.
        #
        # `out` in bad_channel_label is a free brain-surface estimate -- an
        # electrode above the surface is not broken, and conflating the two
        # would discard a usable channel on the next insertion.
        #
        # impedance is nullable because it is CARRIED from wl.works'
        # electrode_reading rather than measured here, and arrives with the
        # activation request or not at all.
        -> master
        -> Clustering.Electrode
        ---
        rms_ap              : float  # (uV) RMS noise, AP band
        rms_lfp             : float  # (uV) RMS noise, LFP band
        bad_channel_label   : enum('good','dead','noise','out')
        line_noise_50hz     : float  # (uV) magnitude at EU mains
        saturation_fraction : float  # fraction of samples at rail
        artifact_fraction   : float
        impedance = null    : float  # (ohm) carried from wl.works
        # Power versus frequency for this channel. A `<blob>` like every other
        # array here -- and small enough to belong in the database by section
        # 6's rule: hundreds of floats per channel, not the ~5.5 GB per stream
        # that keeps LFP and MUA out.
        spectral_profile = null : <blob>
        """


@schema
class LFP(dj.Manual):
    definition = """
    # Provenance for one LFP product. Key: (subject, session_datetime,
    # montage_id, activation_id, insertion_number, paramset_type,
    # paramset_idx).
    #
    # NO SAMPLE ARRAY, deliberately. Parent spec section 8.4 stores every
    # continuous channel at 500 Hz -- 384 KB/s per probe, so ~5.5 GB per 2 h
    # dual-probe session -- and parent spec section 3.3's storage tiers put the
    # NWB on the NAS with no database tier at all. Declaring `lfp : <blob>`
    # here would satisfy the blob rule and still be wrong. Design spec sections
    # 3.4 and 6.
    -> request.Activation
    -> ProbeInsertion
    -> paramset.ParamSet
    ---
    output_rate_hz : float  # 500 is the lab default (parent spec section 8.4)
    artifact_host  : varchar(64)
    artifact_share : varchar(64)
    artifact_path  : varchar(255)
    """


@schema
class MUA(dj.Manual):
    definition = """
    # Provenance for one MUA-envelope product. Same shape and same reasoning as
    # LFP above -- no sample array. Key: (subject, session_datetime,
    # montage_id, activation_id, insertion_number, paramset_type,
    # paramset_idx).
    #
    # The envelope is computed from the 500-5000 Hz band BEFORE any decimation
    # (parent spec section 8.4), and its low-pass must sit at <=200 Hz or the
    # envelope itself aliases at a 500 Hz output rate.
    -> request.Activation
    -> ProbeInsertion
    -> paramset.ParamSet
    ---
    output_rate_hz : float
    artifact_host  : varchar(64)
    artifact_share : varchar(64)
    artifact_path  : varchar(255)
    """


def register_probe_type(part_number: str) -> None:
    """Declare `part_number` and every electrode of it. Idempotent.

    The geometry lookup runs FIRST and nothing is inserted until it
    succeeds. `UnknownProbeType` exists to stop a `ProbeType` existing with
    no electrodes -- "every downstream foreign key resolve[s] against an
    empty set, and nothing would report a problem" -- and inserting the
    master row before the lookup would raise that exception having already
    created exactly the row it forbids.
    """
    rows = [
        {"probe_type": part_number, **row}
        for row in geometry.electrode_rows(part_number)
    ]
    ProbeType.insert1({"probe_type": part_number}, skip_duplicates=True)
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


def link_insertions(session_key: dict) -> int:
    """Join wl.works' report of each insertion to the probe the recording
    names, for one session: `ProbeInsertion`, `InsertionLocation` and
    `SegmentConfig` (design spec `2026-09-30-nwb-probes-design.md` section
    2.3). Returns how many insertions' links changed.

    Written as the difference between what the reports and the census say
    now and what is linked, so it can run on every pass, in either arrival
    order, and a corrected report relinks: a changed trajectory or aim is
    updated in place, and a changed serial moves the insertion's segments.

    **Linked only when the serial is reported for ONE insertion of the
    session, and the census placed its sites** -- `ProbeInsertion` needs a
    `Probe`, and `SegmentConfig` a config. A serial reported twice is a moved
    probe whose segments the request does not assign, and is not guessed at.

    **It takes back only links it could have made.** Reports are never
    deleted, so every insertion it linked has one; an insertion with no report
    was made some other way and is left alone. A link taken back is deleted
    with `delete_quick`, which does not cascade: an insertion that something
    downstream already points at -- a sort -- raises instead of taking the
    sort with it."""
    reports = (InsertionReport & session_key).to_dicts()
    serials = [report["probe_serial"] for report in reports]
    recorded: dict[str, dict] = {}
    for part in (ProbeCensus.Probe & session_key & "electrode_config_hash IS NOT NULL").to_dicts():
        if part["probe_serial"]:
            segment = (part["system"], part["segment_barcode"])
            recorded.setdefault(part["probe_serial"], {})[segment] = (part["electrode_config_hash"], part["probe_type"])
    want = {
        report["insertion_number"]: report
        for report in reports
        if serials.count(report["probe_serial"]) == 1 and report["probe_serial"] in recorded
    }
    reported = {int(report["insertion_number"]) for report in reports}
    changed = 0
    with dj.conn().transaction:
        for number in (ProbeInsertion & session_key).to_arrays("insertion_number"):
            if int(number) in reported and int(number) not in want:
                where = {**session_key, "insertion_number": int(number)}
                (SegmentConfig & where).delete_quick()
                (InsertionLocation & where).delete_quick()
                (ProbeInsertion & where).delete_quick()
                changed += 1
        for number, report in want.items():
            changed += _link(
                {**session_key, "insertion_number": number}, report, recorded[report["probe_serial"]]
            )
    return changed


def _link(where: dict, report: dict, segments: dict) -> int:
    """One insertion's three kinds of row, brought to what `report` and the
    census say. 1 if anything changed, else 0."""
    changed = False

    def settle(table, want: dict | None, have: list[dict]) -> None:
        """Make `table`'s one row here `want`, or remove it when `want` is None."""
        nonlocal changed
        if want is None and have:
            (table & {name: have[0][name] for name in table.primary_key}).delete_quick()
        elif want is not None and not have:
            table.insert1(want)
        elif want is not None and any(have[0][name] != want[name] for name in want):
            table.update1(want)
        else:
            return
        changed = True

    settle(ProbeInsertion, {**where, "probe_serial": report["probe_serial"], "trajectory_id": report["trajectory_id"]},
           (ProbeInsertion & where).to_dicts())
    aim = None if report["target_area"] is None else {
        **where, "area": report["target_area"], "atlas": report["target_atlas"],
        "atlas_level": report["target_atlas_level"]}
    settle(InsertionLocation, aim, (InsertionLocation & where).to_dicts())
    have = {(row["system"], row["segment_barcode"]): row for row in (SegmentConfig & where).to_dicts()}
    for segment in have.keys() | segments.keys():
        want = None if segment not in segments else {
            **where, "system": segment[0], "segment_barcode": segment[1],
            "electrode_config_hash": segments[segment][0], "probe_type": segments[segment][1]}
        settle(SegmentConfig, want, [have[segment]] if segment in have else [])
    return int(changed)


class EmptyElectrodeIntersection(dj.DataJointError):
    """No electrode is common to every configuration named.

    Raised rather than returning a zero-electrode config: design spec section
    3.2.2 refuses such an activation at request time, the way an uncoverable
    block set already is, and a zero-electrode config would let a sort be
    requested over nothing.
    """


def intersect_electrode_configs(hashes: list[str]) -> str:
    """The config holding exactly the electrodes common to every `hashes` entry.

    For a canonical activation, whose segments all share one configuration,
    this returns that same hash -- the sorted-set hash of Task 2 makes the
    single-input case an identity rather than a copy. For a cross-montage
    derivative it mints the intersection, which is an `ElectrodeConfig` row
    like any other. That is the whole of design spec section 3.2.2's claim that
    the two cases need no branch.
    """
    if not hashes:
        raise EmptyElectrodeIntersection("no electrode configurations were named")

    probe_types = set(
        (ElectrodeConfig & [{"electrode_config_hash": h} for h in hashes]).to_arrays(
            "probe_type"
        )
    )
    if len(probe_types) != 1:
        raise EmptyElectrodeIntersection(
            f"expected exactly one probe type across the configurations named, "
            f"got {sorted(probe_types)} -- an intersection across probe models is "
            f"not meaningful, because electrode numbers name different physical "
            f"sites on each. An empty list here means a named hash is not in "
            f"ElectrodeConfig at all."
        )
    part_number = str(probe_types.pop())

    shared: set[int] | None = None
    for h in hashes:
        electrodes = {
            int(e)
            for e in (
                ElectrodeConfig.Electrode & {"electrode_config_hash": h}
            ).to_arrays("electrode")
        }
        shared = electrodes if shared is None else (shared & electrodes)

    if not shared:
        raise EmptyElectrodeIntersection(
            f"no electrode is common to all {len(hashes)} configurations"
        )

    return register_electrode_config(part_number, sorted(shared))


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}ephys`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}ephys", create_tables=True)
