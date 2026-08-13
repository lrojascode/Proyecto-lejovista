"""Tests de la máquina de estados pura del chat de creación de campañas (CH-1)."""
from app.services.campaign_chat.machine import Invalidation, SessionFacts, invalidation_for_changed_slots, next_state


def _facts(**overrides):
    base = dict(
        goal_text=None, image_path=None, video_path=None,
        has_proposal=False, proposal_accepted=False, draft_id=None,
    )
    base.update(overrides)
    return SessionFacts(**base)


def test_empty_session_asks_goal_first():
    assert next_state(_facts()) == "ASK_GOAL"


def test_goal_set_asks_for_media():
    assert next_state(_facts(goal_text="vender zapatillas")) == "ASK_MEDIA"


def test_two_slots_in_one_message_skips_straight_to_build_plan():
    # goal_text + image_path llegaron juntos -> next_state salta directo a BUILD_PLAN
    assert next_state(_facts(goal_text="vender zapatillas", image_path="org/x.jpg")) == "BUILD_PLAN"


def test_video_counts_as_media_too():
    assert next_state(_facts(goal_text="vender zapatillas", video_path="org/x.mp4")) == "BUILD_PLAN"


def test_proposal_built_but_not_accepted_shows_review():
    facts = _facts(goal_text="g", image_path="p", has_proposal=True, proposal_accepted=False)
    assert next_state(facts) == "REVIEW_PLAN"


def test_proposal_accepted_moves_to_confirm_risk():
    facts = _facts(goal_text="g", image_path="p", has_proposal=True, proposal_accepted=True)
    assert next_state(facts) == "CONFIRM_RISK"


def test_draft_id_always_wins_even_with_empty_slots():
    assert next_state(_facts(draft_id="draft-1")) == "CREATING"


def test_invalidation_hard_slot_clears_everything():
    inv = invalidation_for_changed_slots(frozenset({"goal_text"}))
    assert inv == Invalidation(clear_proposal=True, clear_acceptance=True, clear_preflight=True)


def test_invalidation_soft_slot_keeps_proposal():
    inv = invalidation_for_changed_slots(frozenset({"budget"}))
    assert inv == Invalidation(clear_proposal=False, clear_acceptance=True, clear_preflight=True)


def test_invalidation_unrelated_slot_is_noop():
    inv = invalidation_for_changed_slots(frozenset({"ad_account_id"}))
    assert inv == Invalidation()


def test_invalidation_mixed_changes_prefers_hard():
    inv = invalidation_for_changed_slots(frozenset({"budget", "image_path"}))
    assert inv.clear_proposal is True
