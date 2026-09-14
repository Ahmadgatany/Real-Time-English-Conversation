import asyncio

from app.services.evaluation.engine import EvaluationEngine


class FakeLanguageModel:
    async def respond(self, messages: list[dict[str, str]]) -> str:
        return '{"grammar_issues":["article choice"],"grammar_complexity":"varied","advanced_vocabulary":["sustainable"],"repeated_words":["good"],"examples":["Use an article before singular count nouns."]}'


def test_speech_metrics_are_deterministic() -> None:
    metrics = EvaluationEngine.speech_metrics(
        "I like travel because travel is good. Um, um, it is exciting.",
        60,
    )
    assert metrics.word_count == 12
    assert metrics.speaking_rate_wpm == 12.0
    assert metrics.filler_count == 3
    assert metrics.repetition_count >= 1
    assert metrics.vocabulary_diversity < 0.8


def test_evaluation_combines_metrics_and_llm_analysis() -> None:
    engine = EvaluationEngine(FakeLanguageModel())
    result = asyncio.run(engine.evaluate(
        [{"role": "user", "text": "I enjoy learning sustainable skills because they help my future."}],
        30,
    ))
    assert result.scores["grammar"] >= 0
    assert result.feedback["linguistic_analysis"]["advanced_vocabulary"] == ["sustainable"]
    assert result.feedback["estimate_notice"]
    assert result.coach_exercises


def test_attempt_comparison_reports_improvements_and_remaining_weaknesses() -> None:
    comparison = EvaluationEngine.compare_attempts(
        {"fluency": 5.0, "grammar": 6.0, "vocabulary": 5.5, "pronunciation": 5.0},
        {"fluency": 6.0, "grammar": 6.0, "vocabulary": 5.0, "pronunciation": 5.5},
    )
    assert "Fluency improved from 5 to 6." in comparison["improvements"]
    assert "vocabulary" in comparison["remaining_weaknesses"]
    assert "grammar" in comparison["remaining_weaknesses"]
