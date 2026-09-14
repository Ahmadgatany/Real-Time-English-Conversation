from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import time
from typing import Any


@dataclass
class Metrics:
    counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    timings: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def increment(self, name: str, value: int = 1) -> None:
        self.counters[name] += value

    def observe(self, name: str, duration_seconds: float) -> None:
        samples = self.timings[name]
        samples.append(round(duration_seconds, 4))
        if len(samples) > 1000:
            del samples[:-1000]

    def snapshot(self) -> dict[str, Any]:
        return {
            "counters": dict(self.counters),
            "timings": {
                name: {
                    "count": len(values),
                    "average_seconds": round(sum(values) / len(values), 4) if values else 0,
                    "max_seconds": max(values, default=0),
                }
                for name, values in self.timings.items()
            },
        }


metrics = Metrics()


def cost_estimate(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    tts_characters: int = 0,
    stt_seconds: int = 0,
) -> dict[str, float]:
    # Provider prices are intentionally configurable at the service boundary later.
    llm = input_tokens * 0.00000030 + output_tokens * 0.00000250
    tts = tts_characters * 0.000015
    stt = stt_seconds * 0.00010
    return {
        "llm_usd": round(llm, 6),
        "tts_usd": round(tts, 6),
        "stt_usd": round(stt, 6),
        "total_usd": round(llm + tts + stt, 6),
    }


def elapsed(start: float) -> float:
    return time.perf_counter() - start
