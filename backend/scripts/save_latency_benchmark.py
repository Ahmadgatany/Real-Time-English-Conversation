"""Store a frontend latency export as a unique run and before/after report."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = ROOT / "benchmarks" / "latency" / "baseline.json"
RUNS_PATH = ROOT / "benchmarks" / "latency" / "runs"

METRICS = {
    "end_of_speech_to_first_text_ms": "End of speech to first text (browser clock)",
    "backend_end_of_speech_to_first_gemini_audio_ms": "End of speech to first Gemini audio (backend clock)",
    "backend_end_of_speech_to_first_audio_sent_ms": "End of speech to first backend audio send (backend clock)",
    "frontend_end_of_speech_to_first_audio_ms": "End of speech to first frontend audio receipt (browser clock)",
    "frontend_end_of_speech_to_playback_start_ms": "End of speech to playback startup (browser clock)",
    "backend_audio_generation_complete_ms": "Gemini audio generation complete (backend clock)",
    "backend_first_gemini_audio_to_generation_complete_ms": "First Gemini audio to generation completion (backend clock)",
    "backend_gemini_first_chunk_to_first_send_ms": "First Gemini chunk to first backend send (backend clock)",
    "backend_last_audio_send_to_generation_complete_ms": "Last backend chunk send to generation completion (backend clock)",
    "frontend_playback_complete_ms": "Playback complete (browser clock)",
}
BASELINE_REFERENCE_KEYS = {
    "end_of_speech_to_first_text_ms": "ttft",
    "backend_end_of_speech_to_first_gemini_audio_ms": "first_audio_from_gemini",
    "frontend_end_of_speech_to_first_audio_ms": "first_audio_from_gemini",
    "frontend_end_of_speech_to_playback_start_ms": "playback_start",
    "frontend_playback_complete_ms": "playback_complete",
}


def summarize(values: list[float]) -> dict[str, float | int] | None:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None

    def percentile(p: float) -> float:
        return clean[max(0, math.ceil(p * len(clean)) - 1)]

    return {
        "count": len(clean),
        "average": round(sum(clean) / len(clean), 2),
        "minimum": round(clean[0], 2),
        "maximum": round(clean[-1], 2),
        "p50": round(percentile(0.50), 2),
        "p95": round(percentile(0.95), 2),
    }


def create_run(export: dict[str, Any], baseline: dict[str, Any], timestamp: str) -> tuple[dict[str, Any], str]:
    turns = export.get("completed_turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("Export must contain at least one completed turn")
    statistics = {
        metric: summarize([turn[metric] for turn in turns if isinstance(turn.get(metric), (int, float))])
        for metric in METRICS
    }
    intervals = [
        interval
        for turn in turns
        for interval in turn.get("frontend_audio_chunk_intervals_ms", [])
        if isinstance(interval, (int, float))
    ]
    statistics["frontend_audio_chunk_interval_ms"] = summarize(intervals)
    backend_intervals = [
        interval
        for turn in turns
        for interval in turn.get("backend_audio_chunk_intervals_ms", [])
        if isinstance(interval, (int, float))
    ]
    statistics["backend_audio_chunk_interval_ms"] = summarize(backend_intervals)
    gemini_intervals = [
        interval
        for turn in turns
        for interval in turn.get("backend_gemini_audio_chunk_intervals_ms", [])
        if isinstance(interval, (int, float))
    ]
    statistics["gemini_audio_chunk_interval_ms"] = summarize(gemini_intervals)

    historical = baseline.get("metrics_ms", {})
    comparisons: dict[str, Any] = {}
    for metric, label in METRICS.items():
        after = statistics[metric]
        if metric == "frontend_playback_complete_ms" and after:
            before = historical.get("playback_complete", {}).get("statistics", {})
            if before.get("average") is not None and before.get("p50") is not None:
                average_delta = round(before["average"] - after["average"], 2)
                p50_delta = round(before["p50"] - after["p50"], 2)
                reductions = {
                    key: {
                        "reduction_ms": round(before[key] - after[key], 2),
                        "reduction_percent": round((before[key] - after[key]) / before[key] * 100, 2) if before[key] else None,
                    }
                    for key in ("average", "minimum", "maximum", "p50", "p95")
                    if before.get(key) is not None and after.get(key) is not None
                }
                comparisons[metric] = {
                    "status": "comparable_historical_aggregate",
                    "label": label,
                    "before": before,
                    "after": after,
                    "average_reduction_ms": average_delta,
                    "average_reduction_percent": round(average_delta / before["average"] * 100, 2) if before["average"] else None,
                    "p50_reduction_ms": p50_delta,
                    "p50_reduction_percent": round(p50_delta / before["p50"] * 100, 2) if before["p50"] else None,
                    "statistic_reductions": reductions,
                }
                continue
        comparisons[metric] = {
            "status": "not_comparable_baseline_has_no_distribution",
            "label": label,
            "before_reference_ms": historical.get(BASELINE_REFERENCE_KEYS.get(metric, ""), {}).get("value"),
            "after": after,
            "reduction": None,
        }

    run = {
        "schema_version": 1,
        "run_id": timestamp,
        "source": "new_frontend_capture",
        "baseline_id": baseline.get("baseline_id"),
        "metadata": export.get("metadata", {}),
        "completed_turns": len(turns),
        "metric_statistics": statistics,
        "turns": turns,
        "before_after": comparisons,
        "clock_note": "Backend and browser durations are reported in separate clock domains and are never subtracted from one another.",
    }

    rows = ["# Latency benchmark comparison", "", f"Run: `{timestamp}`", "", "| Metric | Historical baseline | New run | Comparison |", "|---|---:|---:|---|"]
    for metric, label in METRICS.items():
        comparison = comparisons[metric]
        after = statistics[metric]
        after_text = f"avg {after['average']:.2f} ms; P50 {after['p50']:.2f} ms" if after else "unavailable"
        if comparison["status"] == "comparable_historical_aggregate":
            before = comparison["before"]
            reduction = comparison["average_reduction_ms"]
            percent = comparison["average_reduction_percent"]
            before_text = f"avg {before['average']} ms; P50 {before['p50']} ms"
            p95_reduction = comparison["statistic_reductions"].get("p95", {}).get("reduction_ms")
            result = f"average reduction {reduction:+.2f} ms ({percent:+.2f}%); P95 reduction {p95_reduction:+.2f} ms" if p95_reduction is not None else f"average reduction {reduction:+.2f} ms ({percent:+.2f}%)"
        else:
            reference = comparison.get("before_reference_ms")
            before_text = f"reference {reference} ms" if reference is not None else "unavailable"
            result = "not comparable: baseline distribution unavailable"
        rows.append(f"| {label} | {before_text} | {after_text} | {result} |")
    for metric, label in (("gemini_audio_chunk_interval_ms", "Gemini audio chunk intervals (backend clock)"), ("backend_audio_chunk_interval_ms", "Backend audio chunk intervals (backend clock)"), ("frontend_audio_chunk_interval_ms", "Frontend audio chunk intervals (browser clock)")):
        after = statistics[metric]
        after_text = f"avg {after['average']:.2f} ms; P50 {after['p50']:.2f} ms" if after else "unavailable"
        rows.append(f"| {label} | unavailable | {after_text} | no historical baseline |")
    rows.extend([
        "",
        "The baseline is manually supplied historical data. Only playback completion has a historical aggregate distribution; all other baseline values are references without raw turn samples.",
        "No performance improvement is claimed until this run contains collected measurements.",
        "",
    ])
    return run, "\n".join(rows)


def save_export(export_path: Path, *, now: datetime | None = None, runs_path: Path = RUNS_PATH) -> tuple[Path, Path]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    export = json.loads(export_path.read_text(encoding="utf-8"))
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run, report = create_run(export, baseline, timestamp)
    runs_path.mkdir(parents=True, exist_ok=True)
    stem = f"run-{timestamp}"
    run_path = runs_path / f"{stem}.json"
    report_path = runs_path / f"comparison-{timestamp}.md"
    suffix = 1
    while run_path.exists() or report_path.exists():
        run_path = runs_path / f"{stem}-{suffix:02d}.json"
        report_path = runs_path / f"comparison-{timestamp}-{suffix:02d}.md"
        suffix += 1
    run_path.write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    return run_path, report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frontend_export", type=Path, help="JSON downloaded from the conversation diagnostics panel")
    args = parser.parse_args()
    run_path, report_path = save_export(args.frontend_export)
    print(f"Saved benchmark run: {run_path}")
    print(f"Saved comparison report: {report_path}")


if __name__ == "__main__":
    main()
