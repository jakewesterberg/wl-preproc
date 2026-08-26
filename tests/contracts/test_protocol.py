import pytest
from pydantic import ValidationError

from wl_preproc.contracts.protocol import (
    Action,
    HealthResponse,
    JobRequest,
    MetadataBundle,
    Reading,
    contains_markup,
    plain_text,
)

# Verbatim from wl.works Plan 10 section 4.
PLAN_10_EXAMPLE = {
    "verdict": "ok",
    "readings": [
        {"key": "transfer", "label": "Latest transfer", "value": "complete", "featured": False},
        {
            "key": "spike-sort",
            "label": "Spike sorting",
            "value": "4 of 7 sessions",
            "featured": True,
        },
    ],
    "actions": [{"name": "start-preproc", "label": "Start preprocessing run"}],
}


def test_plan_10_example_validates():
    response = HealthResponse.model_validate(PLAN_10_EXAMPLE)
    assert response.verdict == "ok"
    assert response.readings[1].featured is True
    assert response.actions[0].name == "start-preproc"


@pytest.mark.parametrize("text", ["<b>bold</b>", "plain <img src=x>", "a & b", "line<br>break"])
def test_contains_markup_detects(text):
    assert contains_markup(text) is True


@pytest.mark.parametrize("text", ["complete", "4 of 7 sessions", "rig-a: 12 units"])
def test_contains_markup_allows_plain(text):
    assert contains_markup(text) is False


def test_plain_text_removes_every_markup_character():
    result = plain_text("A&B<C>D")
    assert not contains_markup(result), f"still contains markup: {result!r}"


def test_plain_text_never_collapses_two_different_characters_onto_one_placeholder():
    """Every producer of untrusted text (currently `responder/health.py`)
    depends on this: substituting `<`, `>` and `&` onto ONE shared
    placeholder would make `A&B` and `A<B` -- two different facts about a
    real fault -- render identically, which is the exact rule `Reading`'s
    own markup ban already argues from for a different reason.
    """
    substituted = {plain_text("A&B"), plain_text("A<B"), plain_text("A>B")}
    assert len(substituted) == 3, f"two distinct inputs collapsed onto one output: {substituted}"


def test_plain_text_is_a_no_op_on_text_with_no_markup():
    assert plain_text("4 of 7 sessions") == "4 of 7 sessions"


def test_plain_text_output_is_itself_accepted_by_reading():
    """The whole point: the substituted text must be constructible, not just
    markup-free in the abstract."""
    value = plain_text("path 'A&B<C>' denied")
    Reading(key="k", label="L", value=value, featured=False)  # must not raise


def test_reading_rejects_markup_in_label():
    """wl.works treats this host as untrusted and renders labels as escaped text."""
    with pytest.raises(ValidationError):
        Reading(key="k", label="<b>Spike sorting</b>", value="ok", featured=False)


def test_reading_rejects_markup_in_value():
    with pytest.raises(ValidationError):
        Reading(key="k", label="Spike sorting", value="4 <b>of</b> 7", featured=False)


def test_action_rejects_markup_in_label():
    with pytest.raises(ValidationError):
        Action(name="start-preproc", label="Start <i>preprocessing</i>")


def test_health_response_rejects_unknown_key():
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdcit": "ok"})


def test_an_unknown_verdict_is_rejected():
    """wl.works validates verdict against exactly four values and refuses the
    whole response otherwise, so a bare `str` here means a typo ships and
    fails at their end rather than ours. Plan 10 section 1.1."""
    with pytest.raises(ValidationError):
        HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": "okay"})


@pytest.mark.parametrize("verdict", ["ok", "degraded", "down", "unknown"])
def test_every_verdict_wl_works_accepts_validates_here(verdict):
    assert HealthResponse.model_validate({**PLAN_10_EXAMPLE, "verdict": verdict}).verdict == verdict


def test_job_request_carries_the_metadata_bundle():
    """wl-preproc cannot fetch from wl.works, so metadata must arrive inbound."""
    request = JobRequest(
        domain="neural",
        selection={"session_id": "2027-03-14_01", "montage_id": 1},
        parameters={"clustering_paramset": "ks4_default"},
        idempotency_key="a1b2c3",
        metadata=MetadataBundle(
            blocks=[{"block_id": 1, "task_type": "rf_map"}],
            montage_boundaries=[{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
            probes=[{"serial": "NP-1234", "insertion_number": 1}],
            experimenter="jw",
            subject="pico",
            task_types=["rf_map", "resting_dark"],
        ),
    )
    assert request.metadata.subject == "pico"
    assert request.metadata.montage_boundaries[0].montage_id == 1


def test_job_request_rejects_unknown_key():
    with pytest.raises(ValidationError):
        JobRequest.model_validate(
            {
                "domain": "neural",
                "selection": {},
                "parameters": {},
                "idempotency_key": "x",
                "metadata": {
                    "blocks": [],
                    "montage_boundaries": [],
                    "probes": [],
                    "experimenter": "jw",
                    "subject": "pico",
                    "task_types": [],
                },
                "priorty": 3,
            }
        )


# --- The two payload holes, closed 2026-08-26. -------------------------------
#
# Both were named in `handoffs/2026-08-23-next-session-phase-2b-is-hardware-
# blocked.md` and both are the same fault in two places: a `list[dict[str,
# Any]]` records nothing a second implementer can build against. Design spec
# section 11.2 makes this payload a frozen interface precisely because wl.works'
# 18b tests are contract tests against a FAKE wl-preproc, and a fake can only be
# built from what the contract writes down. An untyped list exports to
# `{"type": "object", "additionalProperties": true}` -- which is to say, it
# exports nothing.


def _bundle(**overrides):
    """A minimal valid bundle, so each test below varies exactly one thing."""
    return {
        "blocks": [],
        "montage_boundaries": [{"montage_id": 1, "start_s": 0.0, "end_s": 3600.0}],
        "probes": [{"serial": "NP-1234", "insertion_number": 1}],
        "experimenter": "jw",
        "subject": "pico",
        "task_types": [],
        **overrides,
    }


def test_a_montage_boundary_is_a_model_not_a_bare_dict():
    bundle = MetadataBundle.model_validate(_bundle())
    assert bundle.montage_boundaries[0].montage_id == 1
    assert bundle.montage_boundaries[0].start_s == 0.0
    assert bundle.montage_boundaries[0].end_s == 3600.0


def test_a_montage_id_beyond_tinyint_is_refused_at_the_contract():
    """`core.Montage.montage_id` is a tinyint. 128 does not fit, and the
    refusal belongs in the exported schema where wl.works can see it -- not
    only in `responder/jobs.py`, which nobody outside this repository reads."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 128, "start_s": 0.0, "end_s": 1.0}])
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_montage_boundary_is_refused(bad):
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 0, "start_s": bad, "end_s": 1.0}])
        )


def test_a_montage_boundary_missing_a_field_is_refused():
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(montage_boundaries=[{"montage_id": 0, "start_s": 0.0}])
        )


def test_an_unknown_key_on_a_montage_boundary_is_refused():
    """`extra="forbid"` everywhere else in this module. A frozen interface that
    silently swallows a key it does not know is how two implementations drift
    apart while both look correct."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(
                montage_boundaries=[
                    {"montage_id": 0, "start_s": 0.0, "end_s": 1.0, "bank": "A"}
                ]
            )
        )


def test_a_probe_entry_carries_the_trajectory_it_ran_against():
    """Design spec section 11.2's payload block lists `trajectory_id per
    insertion`; wl-works' trajectory-identity design section 8.1 says the same
    from the other side. `ephys.ProbeInsertion.trajectory_id` has been waiting
    for it since Phase 2a."""
    bundle = MetadataBundle.model_validate(
        _bundle(probes=[{"serial": "NP-1234", "insertion_number": 1, "trajectory_id": "T-0042"}])
    )
    assert bundle.probes[0].trajectory_id == "T-0042"


def test_a_penetration_with_no_trajectory_omits_it_rather_than_inventing_one():
    """Ruled 2026-08-26: probes are sometimes inserted along a trajectory that
    was never planned, so there is nothing to name. Absence is a legitimate
    permanent state, not only a not-yet."""
    bundle = MetadataBundle.model_validate(_bundle())
    assert bundle.probes[0].trajectory_id is None


def test_a_trajectory_id_longer_than_the_column_is_refused():
    """`ephys.ProbeInsertion.trajectory_id` is varchar(64). A 65th character is
    silently truncated by MySQL in non-strict mode, which would store a
    reference to a DIFFERENT trajectory than the one the ELN named."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "trajectory_id": "T" * 65}])
        )


def test_an_unknown_key_on_a_probe_entry_is_refused():
    """The case this exists for: `trajectroy_id`. Under a bare dict it rides
    along unread, and every insertion silently records no trajectory."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 1, "trajectroy_id": "T-1"}])
        )


def test_a_probe_entry_requires_its_serial_and_insertion_number():
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(_bundle(probes=[{"serial": "NP-1"}]))


def test_a_probe_serial_longer_than_the_column_is_refused():
    """`ephys.Probe.probe_serial` is varchar(32)."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "N" * 33, "insertion_number": 1}])
        )


def test_an_insertion_number_beyond_tinyint_unsigned_is_refused():
    """`ephys.ProbeInsertion.insertion_number` is tinyint unsigned."""
    with pytest.raises(ValidationError):
        MetadataBundle.model_validate(
            _bundle(probes=[{"serial": "NP-1", "insertion_number": 256}])
        )
