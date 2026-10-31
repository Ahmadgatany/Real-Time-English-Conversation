import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.save_latency_benchmark import create_run, save_export, summarize


def test_summarize_uses_nearest_rank_percentiles() -> None:
    assert summarize([9, 1, 5, 3, 7]) == {
        "count": 5,
        "average": 5.0,
        "minimum": 1.0,
        "maximum": 9.0,
        "p50": 5.0,
        "p95": 9.0,
    }
    assert summarize([]) is None


def test_historical_comparison_marks_unavailable_distributions() -> None:
    baseline = {
        "baseline_id": "history",
        "metrics_ms": {
            "ttft": {"value": 901, "statistics": None},
            "playback_complete": {"statistics": {"count": 10, "average": 13136, "p50": 13309}},
        },
    }
    export = {"metadata": {"model": "gemini-3.8-live"}, "completed_turns": [
        {"end_of_speech_to_first_text_ms": 600, "frontend_playback_complete_ms": 10_000},
        {"end_of_speech_to_first_text_ms": 700, "frontend_playback_complete_ms": 12_000},
    ]}
    run, report = create_run(export, baseline, "20261030T000000.000000Z")
    assert run["metric_statistics"]["end_of_speech_to_first_text_ms"]["count"] == 2
    assert run["before_after"]["end_of_speech_to_first_text_ms"]["status"] == "not_comparable_baseline_has_no_distribution"
    complete = run["before_after"]["frontend_playback_complete_ms"]
    assert complete["average_reduction_ms"] == 2136
    assert "not comparable" in report


def test_save_export_creates_unique_files_and_keeps_baseline_unchanged(tmp_path: Path, monkeypatch) -> None:
    import scripts.save_latency_benchmark as benchmark

    baseline = {"baseline_id": "immutable", "metrics_ms": {}}
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    export_path = tmp_path / "export.json"
    export_path.write_text(json.dumps({"completed_turns": [{"frontend_playback_complete_ms": 100}]}), encoding="utf-8")
    monkeypatch.setattr(benchmark, "BASELINE_PATH", baseline_path)
    fixed_time = datetime(2026, 10, 30, tzinfo=timezone.utc)

    first = save_export(export_path, now=fixed_time, runs_path=tmp_path / "runs")
    second = save_export(export_path, now=fixed_time, runs_path=tmp_path / "runs")
    assert first[0] != second[0]
    assert first[0].exists() and first[1].exists() and second[0].exists() and second[1].exists()
    assert json.loads(baseline_path.read_text(encoding="utf-8")) == baseline
