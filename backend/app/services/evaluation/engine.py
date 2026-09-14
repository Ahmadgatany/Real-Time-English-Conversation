from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Protocol

from app.services.ai.providers import LanguageModel


class PronunciationAnalyzer(Protocol):
    async def analyze(self, audio_metrics: dict[str, Any] | None) -> dict[str, Any]: ...


class NullPronunciationAnalyzer:
    async def analyze(self, audio_metrics: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "available": False,
            "intelligibility": None,
            "word_stress": None,
            "issues": [],
            "note": "No phoneme-level pronunciation provider is configured yet.",
        }


@dataclass(frozen=True)
class SpeechMetrics:
    word_count: int
    duration_seconds: int
    speaking_rate_wpm: float
    filler_count: int
    repetition_count: int
    pause_count: int
    vocabulary_diversity: float
    sentence_count: int
    average_sentence_length: float
    complex_sentence_ratio: float


@dataclass(frozen=True)
class EvaluationResult:
    scores: dict[str, float]
    overall_band: float
    metrics: dict[str, Any]
    feedback: dict[str, Any]
    recommendations: list[str]
    coach_exercises: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scores"]["overall"] = self.overall_band
        return result


class EvaluationEngine:
    def __init__(
        self,
        language_model: LanguageModel | None = None,
        pronunciation_analyzer: PronunciationAnalyzer | None = None,
    ) -> None:
        self.language_model = language_model
        self.pronunciation_analyzer = pronunciation_analyzer or NullPronunciationAnalyzer()

    async def evaluate(
        self,
        transcript: list[dict[str, str]],
        duration_seconds: int,
        audio_metrics: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        candidate_text = " ".join(
            message["text"] for message in transcript if message.get("role") == "user"
        ).strip()
        metrics = self.speech_metrics(candidate_text, duration_seconds)
        pronunciation = await self.pronunciation_analyzer.analyze(audio_metrics)
        linguistic = await self._linguistic_analysis(candidate_text, metrics)
        scores = self._scores(metrics, linguistic, pronunciation)
        weaknesses = self._weaknesses(scores, metrics, pronunciation)
        recommendations = self._recommendations(weaknesses, metrics)
        exercises = self._exercises(weaknesses)
        feedback = {
            "strengths": self._strengths(scores, metrics),
            "weaknesses": weaknesses,
            "examples": linguistic.get("examples", []),
            "linguistic_analysis": linguistic,
            "pronunciation": pronunciation,
            "estimate_notice": "AI estimate only. This is not an official IELTS score.",
        }
        return EvaluationResult(
            scores=scores,
            overall_band=round(sum(scores.values()) / len(scores), 1),
            metrics={"speech": asdict(metrics), "pronunciation": pronunciation},
            feedback=feedback,
            recommendations=recommendations,
            coach_exercises=exercises,
        )

    @staticmethod
    def speech_metrics(text: str, duration_seconds: int) -> SpeechMetrics:
        words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text.lower())
        word_count = len(words)
        effective_duration = duration_seconds or max(1, round(word_count / 2.0))
        sentences = [part for part in re.split(r"[.!?]+", text) if part.strip()]
        sentence_count = len(sentences) or (1 if words else 0)
        filler_words = {"um", "uh", "erm", "well", "like", "actually", "you know"}
        filler_count = sum(1 for word in words if word in filler_words)
        repetition_count = sum(1 for first, second in zip(words, words[1:]) if first == second)
        unique_count = len(set(words))
        complex_markers = {"because", "although", "while", "which", "that", "however", "therefore"}
        complex_sentences = sum(
            1 for sentence in sentences if complex_markers.intersection(set(re.findall(r"[A-Za-z]+", sentence.lower())))
        )
        return SpeechMetrics(
            word_count=word_count,
            duration_seconds=effective_duration,
            speaking_rate_wpm=round(word_count / effective_duration * 60, 1),
            filler_count=filler_count,
            repetition_count=repetition_count,
            pause_count=max(0, text.count("...")),
            vocabulary_diversity=round(unique_count / word_count, 3) if word_count else 0,
            sentence_count=sentence_count,
            average_sentence_length=round(word_count / sentence_count, 1) if sentence_count else 0,
            complex_sentence_ratio=round(complex_sentences / sentence_count, 3) if sentence_count else 0,
        )

    async def _linguistic_analysis(self, text: str, metrics: SpeechMetrics) -> dict[str, Any]:
        fallback = {
            "grammar_issues": [],
            "grammar_complexity": "basic" if metrics.complex_sentence_ratio < 0.25 else "developing",
            "advanced_vocabulary": [],
            "repeated_words": [],
            "examples": [],
        }
        if not self.language_model or not text:
            return fallback
        prompt = (
            "Analyze this IELTS Speaking candidate transcript. Return JSON only with keys "
            "grammar_issues (array of short strings), grammar_complexity (basic/developing/varied), "
            "advanced_vocabulary (array), repeated_words (array), examples (array of actionable examples). "
            "Do not score pronunciation or invent audio evidence. Transcript:\n" + text
        )
        try:
            response = await self.language_model.respond([{"role": "user", "content": prompt}])
            parsed = self._parse_json(response)
            return {**fallback, **parsed}
        except Exception:
            return fallback

    @staticmethod
    def _parse_json(response: str) -> dict[str, Any]:
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _scores(metrics: SpeechMetrics, linguistic: dict[str, Any], pronunciation: dict[str, Any]) -> dict[str, float]:
        fluency = 5.0 + min(2.0, max(-2.0, (metrics.speaking_rate_wpm - 90) / 60))
        fluency -= min(1.5, metrics.filler_count / max(metrics.word_count, 1) * 8)
        fluency -= min(0.8, metrics.repetition_count / max(metrics.word_count, 1) * 8)
        vocabulary = 4.5 + min(2.5, metrics.vocabulary_diversity * 3)
        vocabulary += min(0.8, len(linguistic.get("advanced_vocabulary", [])) / 8)
        grammar = 4.5 + min(2.5, metrics.complex_sentence_ratio * 4)
        grammar -= min(1.5, len(linguistic.get("grammar_issues", [])) * 0.25)
        pronunciation_score = 5.5
        if pronunciation.get("available") and pronunciation.get("intelligibility") is not None:
            pronunciation_score = 3 + float(pronunciation["intelligibility"]) * 6
        return {key: round(max(0, min(9, value)) * 2) / 2 for key, value in {
            "fluency": fluency,
            "vocabulary": vocabulary,
            "grammar": grammar,
            "pronunciation": pronunciation_score,
        }.items()}

    @staticmethod
    def _strengths(scores: dict[str, float], metrics: SpeechMetrics) -> list[str]:
        strengths = []
        if scores["fluency"] >= 6:
            strengths.append("Your speaking rate supports a steady response flow.")
        if metrics.vocabulary_diversity >= 0.55:
            strengths.append("You used a reasonably varied vocabulary.")
        if metrics.complex_sentence_ratio >= 0.25:
            strengths.append("You attempted connected and complex sentence structures.")
        return strengths or ["You completed the speaking sample and communicated your main ideas."]

    @staticmethod
    def _weaknesses(scores: dict[str, float], metrics: SpeechMetrics, pronunciation: dict[str, Any]) -> list[str]:
        weaknesses = [key for key, score in scores.items() if score < 6]
        if metrics.filler_count > 2 and "fluency" not in weaknesses:
            weaknesses.append("fluency")
        if not pronunciation.get("available") and "pronunciation" not in weaknesses:
            weaknesses.append("pronunciation")
        return weaknesses[:3]

    @staticmethod
    def _recommendations(weaknesses: list[str], metrics: SpeechMetrics) -> list[str]:
        recommendations = []
        if "fluency" in weaknesses:
            recommendations.append("Use a two-second pause instead of fillers, then continue with one clear idea.")
        if "vocabulary" in weaknesses:
            recommendations.append("Replace repeated general words with one precise word before adding another idea.")
        if "grammar" in weaknesses:
            recommendations.append("Join two short ideas with because, although, or which to practise complex sentences.")
        if "pronunciation" in weaknesses:
            recommendations.append("Record a short answer and replay it, checking whether every key word is intelligible.")
        return recommendations or ["Keep extending answers with a reason and a specific example."]

    @staticmethod
    def _exercises(weaknesses: list[str]) -> list[dict[str, Any]]:
        catalog = {
            "fluency": ("Describe a familiar routine without stopping", 60),
            "vocabulary": ("Compare two places, products, or experiences using precise adjectives", 75),
            "grammar": ("Explain a change you would make and give two reasons", 90),
            "pronunciation": ("Describe a memorable event clearly, stressing the important words", 60),
        }
        return [
            {"skill": skill, "prompt": catalog[skill][0], "target_duration_seconds": catalog[skill][1]}
            for skill in weaknesses if skill in catalog
        ]

    @staticmethod
    def compare_attempts(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
        improvements = []
        remaining = []
        for skill in ("fluency", "vocabulary", "grammar", "pronunciation"):
            before = float(previous.get(skill, 0))
            after = float(current.get(skill, 0))
            if after > before:
                improvements.append(f"{skill.title()} improved from {before:g} to {after:g}.")
            elif after <= before:
                remaining.append(skill)
        return {"improvements": improvements, "remaining_weaknesses": remaining}