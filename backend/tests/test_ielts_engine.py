import pytest

from app.services.ielts.engine import IELTSConfig, IELTSExamSession, IELTSState, InvalidExamTransition


def test_ielts_exam_follows_all_states() -> None:
    exam = IELTSExamSession(IELTSConfig(part1_questions=2, part3_questions=2, preparation_seconds=60, long_turn_seconds=120))
    assert exam.start(0).state == IELTSState.INTRO
    assert exam.handle_candidate("My name is Ahmed", 1).state == IELTSState.PART_1
    assert exam.handle_candidate("I start work early", 2).state == IELTSState.PART_1
    cue = exam.handle_candidate("I read in my free time", 3)
    assert cue.state == IELTSState.PART_2
    assert cue.transition == IELTSState.PREPARATION
    preparation = exam.advance_after_prompt(4)
    assert preparation.state == IELTSState.PREPARATION
    long_turn = exam.tick(64)
    assert long_turn is not None and long_turn.state == IELTSState.LONG_TURN
    part3 = exam.tick(184)
    assert part3 is not None and part3.state == IELTSState.PART_3
    assert exam.handle_candidate("People learn for work", 185).state == IELTSState.PART_3
    assert exam.handle_candidate("Schools should help", 186).state == IELTSState.FINISHED


def test_ielts_timer_does_not_advance_early() -> None:
    exam = IELTSExamSession(IELTSConfig(preparation_seconds=10, long_turn_seconds=20))
    exam.start(0)
    exam.handle_candidate("Name and place", 1)
    for now in (2, 3, 4, 5):
        exam.handle_candidate("An answer", now)
    exam.advance_after_prompt(6)
    assert exam.tick(15) is None
    assert exam.tick(16).state == IELTSState.LONG_TURN


def test_ielts_rejects_invalid_transitions() -> None:
    exam = IELTSExamSession()
    with pytest.raises(InvalidExamTransition):
        exam.handle_candidate("Too early", 0)
    exam.start(0)
    with pytest.raises(InvalidExamTransition):
        exam.advance_after_prompt(1)