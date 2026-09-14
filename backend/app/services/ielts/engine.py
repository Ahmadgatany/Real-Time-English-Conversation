from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IELTSState(StrEnum):
    START = "START"
    INTRO = "INTRO"
    PART_1 = "PART_1"
    PART_2 = "PART_2"
    PREPARATION = "PREPARATION"
    LONG_TURN = "LONG_TURN"
    PART_3 = "PART_3"
    FINISHED = "FINISHED"


class InvalidExamTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class IELTSConfig:
    part1_questions: int = 4
    part3_questions: int = 4
    preparation_seconds: int = 60
    long_turn_seconds: int = 120


@dataclass(frozen=True)
class ExamTurn:
    state: IELTSState
    prompt: str | None = None
    deadline_at: float | None = None
    transition: IELTSState | None = None


@dataclass
class IELTSExamSession:
    config: IELTSConfig = field(default_factory=IELTSConfig)
    state: IELTSState = IELTSState.START
    phase_started_at: float | None = None
    deadline_at: float | None = None
    part1_index: int = 0
    part3_index: int = 0
    answers: list[dict[str, Any]] = field(default_factory=list)
    cue_card: dict[str, Any] = field(default_factory=lambda: {
        "topic": "Describe a skill you would like to learn.",
        "points": [
            "what the skill is",
            "why you would like to learn it",
            "how you would learn it",
            "and explain how this skill could help you",
        ],
    })

    INTRO_PROMPT = (
        "Good morning. My name is your examiner. Can you tell me your full name, please? "
        "And what is the name of the place you come from?"
    )
    PART1_QUESTIONS = (
        "Let's talk about your daily routine. What do you usually do in the morning?",
        "What do you enjoy doing in your free time?",
        "How often do you spend time with your friends?",
        "Is there anything you would like to change about your daily life?",
    )
    PART3_QUESTIONS = (
        "Why do you think people choose to learn new skills?",
        "How can schools and workplaces help people develop useful skills?",
        "Do you think technology has changed the way people learn? Why?",
        "What skills do you think will be important in the future?",
    )

    def start(self, now: float) -> ExamTurn:
        self._require(IELTSState.START)
        self._enter(IELTSState.INTRO, now)
        return ExamTurn(self.state, self.INTRO_PROMPT)

    def handle_candidate(self, text: str, now: float) -> ExamTurn:
        clean_text = " ".join(text.split())
        if not clean_text:
            raise InvalidExamTransition("An empty answer cannot advance the exam")
        if self.state in {IELTSState.START, IELTSState.FINISHED}:
            raise InvalidExamTransition(f"Cannot accept an answer while exam is {self.state}")
        if self.state == IELTSState.PREPARATION:
            raise InvalidExamTransition("Preparation time is still running")

        self.answers.append({
            "state": self.state.value,
            "question_index": self.part1_index if self.state == IELTSState.PART_1 else self.part3_index,
            "text": clean_text,
        })
        if self.state == IELTSState.INTRO:
            self._enter(IELTSState.PART_1, now)
            return ExamTurn(self.state, self.PART1_QUESTIONS[0])
        if self.state == IELTSState.PART_1:
            self.part1_index += 1
            if self.part1_index < self.config.part1_questions:
                return ExamTurn(self.state, self.PART1_QUESTIONS[self.part1_index])
            self._enter(IELTSState.PART_2, now)
            return ExamTurn(
                self.state,
                self._cue_card_prompt(),
                transition=IELTSState.PREPARATION,
            )
        if self.state == IELTSState.LONG_TURN:
            return ExamTurn(self.state)
        if self.state == IELTSState.PART_3:
            self.part3_index += 1
            if self.part3_index < self.config.part3_questions:
                return ExamTurn(self.state, self._part3_question())
            self._enter(IELTSState.FINISHED, now)
            return ExamTurn(
                self.state,
                "Thank you. That is the end of the speaking test.",
            )
        raise InvalidExamTransition(f"Unhandled exam state: {self.state}")

    def advance_after_prompt(self, now: float) -> ExamTurn:
        self._require(IELTSState.PART_2)
        self._enter(IELTSState.PREPARATION, now, self.config.preparation_seconds)
        return ExamTurn(self.state, deadline_at=self.deadline_at)

    def tick(self, now: float) -> ExamTurn | None:
        if self.deadline_at is None or now < self.deadline_at:
            return None
        if self.state == IELTSState.PREPARATION:
            self._enter(IELTSState.LONG_TURN, now, self.config.long_turn_seconds)
            return ExamTurn(
                self.state,
                "Now, you have one to two minutes to speak about the topic. I will stop you when the time is up.",
                deadline_at=self.deadline_at,
            )
        if self.state == IELTSState.LONG_TURN:
            self._enter(IELTSState.PART_3, now)
            return ExamTurn(self.state, self._part3_question())
        return None

    def metadata(self) -> dict[str, Any]:
        return {
            "type": "ielts_speaking",
            "state": self.state.value,
            "part1_questions_answered": self.part1_index,
            "part3_questions_answered": self.part3_index,
            "cue_card": self.cue_card,
            "answers": list(self.answers),
        }

    def _part3_question(self) -> str:
        if not self.answers:
            return self.PART3_QUESTIONS[self.part3_index]
        previous = self.answers[-1]["text"].lower()
        if "technology" in previous or "online" in previous:
            return "What are the advantages and disadvantages of learning through technology?"
        if "school" in previous or "work" in previous:
            return "Should learning new skills be a personal responsibility or a social responsibility?"
        return self.PART3_QUESTIONS[self.part3_index]

    def _cue_card_prompt(self) -> str:
        points = "; ".join(self.cue_card["points"])
        return (
            "Here is your task card. You have one minute to prepare. You should talk about "
            f"{self.cue_card['topic']} Include {points}."
        )

    def _enter(self, state: IELTSState, now: float, duration: int | None = None) -> None:
        self.state = state
        self.phase_started_at = now
        self.deadline_at = now + duration if duration is not None else None

    def _require(self, expected: IELTSState) -> None:
        if self.state != expected:
            raise InvalidExamTransition(f"Expected {expected}, current state is {self.state}")
