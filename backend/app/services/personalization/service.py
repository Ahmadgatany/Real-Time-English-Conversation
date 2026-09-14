from __future__ import annotations

from collections import Counter
from typing import Any


SKILLS = ("fluency", "vocabulary", "grammar", "pronunciation")


def build_progress(evaluations: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    score_history = [
        {
            "created_at": item.get("created_at"),
            "scores": item.get("scores", {}),
            "kind": "exam",
        }
        for item in evaluations
    ] + [
        {
            "created_at": item.get("created_at"),
            "scores": item.get("metrics", {}),
            "kind": "practice",
        }
        for item in attempts
    ]
    score_history.sort(key=lambda item: item.get("created_at") or "")
    trends: dict[str, dict[str, float | None]] = {}
    weaknesses = Counter()
    for skill in SKILLS:
        scores = [float(item["scores"][skill]) for item in score_history if skill in item["scores"]]
        trends[skill] = {
            "latest": scores[-1] if scores else None,
            "change": round(scores[-1] - scores[0], 1) if len(scores) > 1 else 0,
        }
        if scores and scores[-1] < 6:
            weaknesses[skill] += 1
    recommendation = recommend_next_activity(trends, weaknesses)
    return {
        "score_history": score_history,
        "trends": trends,
        "recurring_weaknesses": [skill for skill, _ in weaknesses.most_common()],
        "recommended_next": recommendation,
    }


def recommend_next_activity(trends: dict[str, dict[str, float | None]], weaknesses: Counter[str]) -> dict[str, Any]:
    candidates = [skill for skill in SKILLS if trends.get(skill, {}).get("latest") is not None]
    skill = weaknesses.most_common(1)[0][0] if weaknesses else (candidates[0] if candidates else "fluency")
    activities = {
        "fluency": ("Speak about a familiar routine without stopping", 60),
        "vocabulary": ("Compare two experiences using precise vocabulary", 75),
        "grammar": ("Explain a change using two connected reasons", 90),
        "pronunciation": ("Describe a memorable event with clear word stress", 60),
    }
    prompt, duration = activities[skill]
    return {"skill": skill, "prompt": prompt, "target_duration_seconds": duration}
