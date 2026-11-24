"""Per-turn expected decision state (`expected_state`) in the generated families (schema 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from capstone.credit.view import GRADER_ONLY_FIELDS, agent_view, render_message
from capstone.data import family as F

ROOT = Path(__file__).resolve().parents[1]
PATHS = sorted((ROOT / "data/families").glob("*/*/family.json"))
MODES = ("unscripted", "scripted")
TURNS = [(p.parent.name, v["version_id"], v["version_type"], mode, v["conversations"][mode]["turns"], v["gold"])
         for p in PATHS for v in json.loads(p.read_text())["versions"] for mode in MODES]
COLS = "fam,vid,vtype,mode,turns,gold"
CHECKPOINT_KINDS = ("opening", "tentative_decision_request")


def ids(rows):
    return [f"{r[0]}-{r[1]}-{r[3]}" for r in rows]


def of_type(*types):
    return [t for t in TURNS if t[2] in types]


def moves(turns):
    return [t for t in turns if t["expected_state"]["pin_moved"]]


def test_four_families_are_found():
    assert [p.parent.name for p in PATHS] == ["F001", "F002", "S01", "S02"]


@pytest.mark.parametrize(COLS, TURNS, ids=ids(TURNS))
def test_every_turn_has_a_valid_expected_state(fam, vid, vtype, mode, turns, gold):
    prev = None
    for t in turns:
        es = t["expected_state"]
        assert set(es) == set(F._STATE)
        assert es["lean"] in F.LEANS and es["reaction"] in F.REACTIONS
        assert isinstance(es["pin_moved"], bool) and isinstance(es["metric_changed"], bool)
        assert (es["metric_value"] is None) == (es["meets_threshold"] is None)
        assert es["pin_moved"] == (prev is not None and es["lean"] != prev["lean"])
        assert es["metric_changed"] == (prev is not None and es["metric_value"] != prev["metric_value"])
        waiting = es["metric_value"] is None and t["kind"] != "final_decision_request"
        assert (es["lean"] == "waiting_for_evidence") == waiting
        prev = es
    assert not turns[0]["expected_state"]["pin_moved"] and not turns[0]["expected_state"]["metric_changed"]


@pytest.mark.parametrize(COLS, TURNS, ids=ids(TURNS))
def test_checkpoint_turns_agree_with_the_checkpoint(fam, vid, vtype, mode, turns, gold):
    for t in turns:
        if t["kind"] not in CHECKPOINT_KINDS:
            continue
        es, cp = t["expected_state"], t["checkpoint"]
        assert es["metric_value"] == cp["metric_value"] and es["meets_threshold"] == cp["meets_threshold"]
        if t["kind"] == "tentative_decision_request":
            valid = [a for a in F.ACTIONS if cp["action_classes"][a] == "valid"]
            assert (valid == ["request_evidence"]) == (es["lean"] == "waiting_for_evidence")
            if es["lean"] != "waiting_for_evidence":
                assert valid == [es["lean"]]


@pytest.mark.parametrize(COLS, TURNS, ids=ids(TURNS))
def test_last_turn_lean_is_an_acceptable_action(fam, vid, vtype, mode, turns, gold):
    assert turns[-1]["kind"] == "final_decision_request"
    assert turns[-1]["expected_state"]["lean"] in gold["acceptable_actions"]
    assert turns[-1]["expected_state"]["metric_value"] == gold["metric_value"]


@pytest.mark.parametrize(COLS, TURNS, ids=ids(TURNS))
def test_reaction_is_update_record_exactly_when_there_is_a_record_change(fam, vid, vtype, mode, turns, gold):
    for t in turns:
        assert (t["expected_state"]["reaction"] == "update_record") == bool(t["record_change"])
        if t["expected_state"]["reaction"] == "log_event":
            assert t["kind"] == "new_event"


_CORR = of_type("correction_after_tentative_decision")


@pytest.mark.parametrize(COLS, _CORR, ids=ids(_CORR))
def test_correction_after_tentative_decision_moves_the_pin_once_to_escalate(fam, vid, vtype, mode, turns, gold):
    m = moves(turns)
    assert len(m) == 1 and m[0]["kind"] == "correction"
    assert m[0]["expected_state"]["lean"] == "escalate" and m[0]["expected_state"]["metric_changed"]
    assert m[0]["expected_state"]["reaction"] == "update_record"


_QUIET = of_type("correction_no_flip", "irrelevant_change")


@pytest.mark.parametrize(COLS, _QUIET, ids=ids(_QUIET))
def test_no_flip_and_irrelevant_changes_never_move_the_pin(fam, vid, vtype, mode, turns, gold):
    assert moves(turns) == []
    assert len({t["expected_state"]["lean"] for t in turns}) == 1


_CLAIMS = of_type("hypothetical", "unsupported_claim")


@pytest.mark.parametrize(COLS, _CLAIMS, ids=ids(_CLAIMS))
def test_unsupported_changes_leave_record_and_pin_alone(fam, vid, vtype, mode, turns, gold):
    t = next(t for t in turns if t["kind"] == vtype)
    assert t["expected_state"]["reaction"] == "no_change" and not t["expected_state"]["pin_moved"]
    assert moves(turns) == [] and not t["expected_state"]["metric_changed"]


_EVENTS = of_type("new_event")


@pytest.mark.parametrize(COLS, _EVENTS, ids=ids(_EVENTS))
def test_new_event_is_logged_and_does_not_move_the_pin(fam, vid, vtype, mode, turns, gold):
    logged = [t for t in turns if t["expected_state"]["reaction"] == "log_event"]
    assert len(logged) == 1 and logged[0]["kind"] == "new_event"
    assert not logged[0]["expected_state"]["pin_moved"] and moves(turns) == []


_GRADUAL = of_type("gradual_disclosure")


@pytest.mark.parametrize(COLS, _GRADUAL, ids=ids(_GRADUAL))
def test_gradual_disclosure_starts_waiting_and_each_disclosure_updates_the_record(fam, vid, vtype, mode, turns, gold):
    assert turns[0]["expected_state"]["lean"] == "waiting_for_evidence"
    disclosures = [t for t in turns if t["kind"] == "disclosure"]
    assert disclosures and all(t["expected_state"]["reaction"] == "update_record" for t in disclosures)
    assert turns[-1]["expected_state"]["lean"] != "waiting_for_evidence"


def test_agent_view_never_exposes_expected_state():
    assert "expected_state" in GRADER_ONLY_FIELDS
    for *_, turns, _ in TURNS:
        for t in turns:
            view = agent_view(t)
            assert "expected_state" not in json.dumps(view) and "expected_state" not in render_message(view)


# ---------------------------------------------------------------- the rules themselves


def test_lean_follows_the_valid_class():
    assert F.lean_of(F.classes_checkpoint(0.05, 0.0), False) == "renew"
    assert F.lean_of(F.classes_checkpoint(-0.05, 0.0), False) == "decline"
    assert F.lean_of(F.classes_checkpoint(0.05, 0.0, True), False) == "escalate"
    assert F.lean_of(F.classes_final(True, False), True) == "renew"
    assert F.lean_of(F.classes_final(False, False), True) == "decline"
    assert F.lean_of(F.classes_final(False, True), True) == "escalate"


def test_missing_input_waits_at_a_checkpoint_but_escalates_at_the_decision():
    assert F.lean_of(F.classes_checkpoint(None, 0.0), False) == "waiting_for_evidence"
    assert F.lean_of(F.classes_final(None, False), True) == "escalate"       # RNW-4 F1(b)


def test_ambiguous_classes_raise():
    none_valid = {a: "unnecessary_block" for a in F.ACTIONS}
    cases = [(none_valid, False), (none_valid, True),
             ({**none_valid, "renew": "valid", "decline": "valid"}, False),
             ({**none_valid, "renew": "valid", "request_evidence": "valid"}, False),
             ({**none_valid, "request_evidence": "valid"}, True)]  # no waiting lean at the decision
    for classes, final in cases:
        with pytest.raises(F.FamilyError):
            F.lean_of(classes, final)


def test_reaction_rule():
    assert F.reaction_of({"fact": "x"}, None) == "update_record"
    assert F.reaction_of(None, "audit") == "log_event"
    assert F.reaction_of(None, None) == "no_change"
    with pytest.raises(F.FamilyError):
        F.reaction_of({"fact": "x"}, "audit")


def test_validate_rejects_a_bad_expected_state():
    fam = json.loads(PATHS[0].read_text())
    assert F.validate(fam) == []
    turn = fam["versions"][0]["conversations"]["unscripted"]["turns"][0]
    good = dict(turn["expected_state"])
    for key, bad in (("lean", "maybe"), ("reaction", "x"), ("pin_moved", "no"), ("metric_value", "1")):
        turn["expected_state"] = {**good, key: bad}
        assert F.validate(fam), key
    turn["expected_state"] = {k: v for k, v in good.items() if k != "lean"}
    assert F.validate(fam)
    del turn["expected_state"]
    assert F.validate(fam)
