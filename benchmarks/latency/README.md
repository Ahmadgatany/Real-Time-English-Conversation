# Voice latency benchmarks

`baseline.json` contains manually supplied historical values from 10 completed turns. It has no raw per-turn samples and must remain unchanged.

## Record a new run

1. Complete the benchmark turns in the app and open the **Voice latency** diagnostics.
2. Choose **Download latency benchmark JSON**. The export contains timing values only, with no transcript or audio.
3. From the backend directory, run:

   ```powershell
   python scripts/save_latency_benchmark.py <path-to-downloaded-export.json>
   ```

The command creates a unique JSON run under `benchmarks/latency/runs/` and a timestamp-matched Markdown before/after comparison. It reads the historical baseline but never writes it. Timestamp collisions get a numeric suffix instead of overwriting prior files.

Backend durations use the backend monotonic clock; frontend durations use the browser performance clock. The report does not compare or subtract timestamps across those clock domains. Only playback-completion averages and percentiles have a comparable historical distribution in the supplied baseline. Other historical figures are shown as references without calculated reductions.
