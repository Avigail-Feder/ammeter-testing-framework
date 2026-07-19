# Ammeter Testing Framework

This project provides emulators for three types of ammeters — Greenlee, ENTES, and
CIRCUTOR — plus a unified testing framework that samples current measurements from
them, computes statistics, archives results, and compares accuracy/consistency across
ammeter types.

Each ammeter emulator runs a small TCP server on its own thread and responds to a
specific text command with a simulated current reading.

## Project Structure

- `main.py` — starts all three ammeter emulator servers and pulls one measurement from
  each via `client.py`. Good smoke test that the emulators are reachable.
- `Ammeters/`
  - `base_ammeter.py` — base class all emulators inherit from (socket server loop,
    command matching).
  - `Greenlee_Ammeter.py`, `Entes_Ammeter.py`, `Circutor_Ammeter.py` — emulator
    subclasses, one per ammeter type.
  - `client.py` — simple socket client used by `main.py` to request one measurement.
- `config/`
  - `config.yaml` — ammeter ports/commands, sampling settings, analysis metrics, and
    result-archiving settings. Read by `AmmeterTestFramework`.
- `src/`
  - `testing/`
    - `test_framework.py` — `AmmeterTestFramework`: the unified testing API. Samples
      measurements, computes statistics, archives results, and compares ammeters.
    - `visualizer.py` — generates line/histogram plots per ammeter and a comparison
      bar chart across ammeters, saved as PNG files. Used automatically by
      `test_framework.py` when `analysis.visualization.enabled` is true in
      `config.yaml`.
  - `utils/`
    - `config.py` — loads and parses `config.yaml`.
    - `logger.py` — `TestLogger`: logs to both console and a per-run file under
      `results/logs/`.
    - `Utils.py` — shared helpers (e.g. `generate_random_float`, used by the emulators).
- `results/`
  - `logs/` — per-run text logs (created automatically).
  - `runs/` — archived JSON results, one file per test run plus one per comparison run
    (created automatically).
  - `plots/` — generated PNG charts, one line + histogram per ammeter run and one bar
    chart per comparison run (created automatically, only if visualization is enabled).
- `examples/run_tests.py` — an earlier, broken draft. **Not used** — kept only for
  reference. Use `AmmeterTestFramework` directly instead (see below).
- `Exam/` — the original exercise specification.

## Ammeter Reference

| Ammeter   | Port | Command                                   | Measurement Principle           |
|-----------|------|--------------------------------------------|----------------------------------|
| Greenlee  | 5001 | `MEASURE_GREENLEE -get_measurement`        | Ohm's Law: I = V / R             |
| ENTES     | 5002 | `MEASURE_ENTES -get_data`                  | Hall Effect: I = B * K           |
| CIRCUTOR  | 5003 | `MEASURE_CIRCUTOR -get_measurement -current` | Rogowski Coil Integration: I = ∫V dt |

> Note: ports and commands above are the actual values used by the code and
> `config.yaml`. They differ from an earlier draft of this README (which listed
> 5000/5001/5002) — that was a documentation error in the original spec, not in
> the code.

## Setup

Install dependencies:
```sh
pip install -r requirements.txt
```
The project uses `pyyaml` for configuration, `matplotlib` for the enabled plotting
feature, and `pytest` for the automated test suite.

### Run automated tests

```sh
pytest
```

The suite includes unit tests for statistical calculations, error recording,
archiving, and comparison logic, plus integration tests that start all three
emulators on temporary ports and validate their wire protocol through the unified
framework API.

## Usage

### 1. Smoke test the emulators

```sh
python main.py
```
Starts all three emulator servers and prints one raw measurement from each. Useful to
confirm the emulators themselves are working before running the full test framework.

### 2. Run the test framework

The framework needs the emulator servers running in the same process (it doesn't
attach to `main.py`'s servers — each script starts its own). A minimal script:

```python
import threading
import time

from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from src.testing.test_framework import AmmeterTestFramework

threading.Thread(target=lambda: GreenleeAmmeter(5001).start_server(), daemon=True).start()
threading.Thread(target=lambda: EntesAmmeter(5002).start_server(), daemon=True).start()
threading.Thread(target=lambda: CircutorAmmeter(5003).start_server(), daemon=True).start()
time.sleep(1)  # let servers finish binding before the first request

framework = AmmeterTestFramework()

# Test a single ammeter type
result = framework.run_test("greenlee")
print(result["statistics"])

# Test all configured ammeter types and compare them
combined = framework.run_all_tests()
print(combined["comparison"])
```

Run from the project root (`Test_QA_expanded/`) so the `src`/`Ammeters` imports
resolve correctly.

### 3. Configuration

All sampling, ammeter, analysis, and archiving behavior is driven by
`config/config.yaml`:

```yaml
testing:
  sampling:
    measurements_count: 10      # samples per test run
    total_duration_seconds: 10  # informational; ~ measurements_count / sampling_frequency_hz
    sampling_frequency_hz: 1    # samples per second

ammeters:
  greenlee: { port: 5001, command: "MEASURE_GREENLEE -get_measurement" }
  entes:    { port: 5002, command: "MEASURE_ENTES -get_data" }
  circutor: { port: 5003, command: "MEASURE_CIRCUTOR -get_measurement -current" }

analysis:
  statistical_metrics: [mean, median, std_dev, min, max]
  visualization:
    enabled: true
    plot_types: [line, histogram]

result_management:
  storage_format: json
  results_dir: "results/runs"
  keep_history: true
```

Change `measurements_count` / `sampling_frequency_hz` to run longer or shorter tests
without touching any code. Add another ammeter under `ammeters:` (matching port +
exact command string from its emulator class) and `run_all_tests()` will pick it up
automatically.

## Output

Each call to `run_test(ammeter_type)` returns (and archives to
`results/runs/<timestamp>_<ammeter>_<run_id>.json`) a dict like:

```json
{
  "run_id": "63ee2a31-...",
  "ammeter_type": "greenlee",
  "started_at": "...",
  "finished_at": "...",
  "requested_measurements": 10,
  "successful_measurements": 10,
  "failed_measurements": 0,
  "samples": [0.101, 0.087, ...],
  "errors": [],
  "statistics": {
    "mean": 0.089, "median": 0.075, "std_dev": 0.044, "min": 0.035, "max": 0.185
  }
}
```

`run_all_tests()` additionally returns/archives a `comparison` block with each
ammeter's mean current, standard deviation, and coefficient of variation
(std_dev / mean), plus a `most_consistent_ammeter` field. See
`DESIGN_DECISIONS.md` for why coefficient of variation is used instead of a direct
accuracy comparison.

## Visualization

When `analysis.visualization.enabled: true` in `config.yaml`, every `run_test()` call
also generates PNG charts under `results/plots/` for whichever plot types are listed
in `analysis.visualization.plot_types`:

- **line** — samples plotted in order, with the run's mean overlaid as a reference line.
- **histogram** — distribution of the sampled values.

`run_all_tests()` additionally generates one comparison bar chart (mean current per
ammeter, with standard-deviation error bars), regardless of `plot_types`. Note that
because ENTES readings run roughly 100-1000x larger than Greenlee/CIRCUTOR, the
smaller two bars appear visually flattened on that chart — it's still accurate, just
harder to read at a glance; see `DESIGN_DECISIONS.md` for more on this.

Plot file paths are recorded back into the returned/archived result
(`result["plots"]`, `combined["comparison_plot"]`), so each archived JSON file
references its own generated images. A plotting failure is logged but never crashes
a test run — visualization is best-effort and non-critical to the measurement itself.

## Error Simulation (Bonus)

To demonstrate the framework's error handling under real fault conditions, set
`error_simulation.enabled: true` in `config.yaml`:

```yaml
error_simulation:
  enabled: true
  failure_probability: 0.2
  failure_types:
    - connection_refused
    - timeout
    - malformed_data
```

With this enabled, each sample has a `failure_probability` chance of being replaced
with a simulated fault (chosen randomly from `failure_types`) instead of a real
measurement — exercising the exact same error-handling path a genuine ammeter failure
would trigger. Simulated failures are logged with a `[SIMULATED]` prefix so they're
never confused with real failures, and still show up in the result's
`failed_measurements` count and `errors` list.

**Leave `enabled: false` (the default) for normal runs** — including any results you
intend to keep as accuracy/consistency evidence — since simulated failures will
reduce `successful_measurements` and are not representative of real ammeter behavior.

## Logs

Every test run also writes a human-readable log to `results/logs/`, including every
individual sample and any errors encountered, in addition to printing to console.

## Known Issues / Notes

- `examples/run_tests.py` is broken (calls `framework.run_test()` with no ammeter
  type, and has other issues) — left in place only as a reference per the original
  instructions, not meant to be run.
- The emulators generate their underlying physical parameters (voltage, resistance,
  magnetic field, etc.) randomly on every call, so results — including which ammeter
  looks "most consistent" — will vary between runs, especially with small sample
  sizes. Increase `measurements_count` for more stable statistics.
