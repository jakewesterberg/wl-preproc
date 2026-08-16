"""The derived action list. Design spec section 3: today it is empty, and that is correct."""

from __future__ import annotations


def test_no_computed_stage_means_no_actions(dj_conn, prefix):
    """Section 3: wl.works renders each action as a button any lab member can
    press. A button that queues work nothing will pick up for six months is a
    button that teaches people to distrust the surface."""
    from wl_preproc.responder.actions import available_actions

    assert available_actions(prefix=prefix) == []


def test_an_action_appears_when_its_stage_does(dj_conn, prefix, monkeypatch):
    """The property the whole design rests on: Phase 2 lands, spike sorting
    appears, and neither this file nor wl.works changes."""
    from wl_preproc.responder import actions

    monkeypatch.setattr(actions, "_stage_domains", lambda prefix: {"neural"})
    published = actions.available_actions(prefix=prefix)

    assert [a.name for a in published] == ["neural"]
    assert published[0].label == actions.DOMAIN_LABELS["neural"]
