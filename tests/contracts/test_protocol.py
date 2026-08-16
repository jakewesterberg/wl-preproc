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
    assert request.metadata.montage_boundaries[0]["montage_id"] == 1


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
