# Design Decisions & Bug Fixes

## Libraries Installed

- **pyyaml** — required to parse `config/config.yaml`. Was already listed in
  `requirements.txt` but not present in the environment; installed via
  `pip install pyyaml`.
- **pytest** — development/test dependency used by the automated unit and
  emulator-integration test suite in `tests/`.

No other runtime dependencies were added. `AmmeterTestFramework` intentionally uses only
the Python standard library (`socket`, `json`, `statistics`, `uuid`, `time`,
`datetime`, `logging`) to satisfy the exercise's "minimize external library
dependencies" constraint.

## Bugs Found & Fixed

### 1. `main.py` — client sent commands that could never match

**Symptom:** the three `request_current_from_ammeter(...)` calls were commented out
with a note that they "shouldn't work."

**Root cause:** each emulator's `start_server()` (in `base_ammeter.py`) does an exact
byte-for-byte comparison between the incoming request and `get_current_command`. The
original (commented-out) calls sent truncated commands, e.g. `b'MEASURE_GREENLEE'`,
but the Greenlee emulator's actual expected command is
`b'MEASURE_GREENLEE -get_measurement'`. A partial match never satisfies `==`, so the
server never sends a response and the client call would hang until timeout.

**Fix:** uncommented the calls and sent the full, correct command string for each
ammeter, matching what each emulator subclass actually defines:
- Greenlee: `MEASURE_GREENLEE -get_measurement`
- ENTES: `MEASURE_ENTES -get_data`
- CIRCUTOR: `MEASURE_CIRCUTOR -get_measurement -current`

Also worth noting: `main.py` uses ports 5001/5002/5003, while the top-level README
(as originally written) listed 5000/5001/5002 for the same ammeters. This is a
documentation inconsistency in the original spec, not a code bug — the code was
internally consistent, so no functional fix was needed here, only correcting the
docs (see updated `README.md`).

### 2. `src/utils/logger.py` — logger had no output handler

**Symptom:** `TestLogger.info()` / `.error()` / etc. ran without error but produced no
visible output and no log file content, even though a `log_file` path was being
constructed.

**Root cause:** `_setup_logger()` created a `logging.Logger` instance but never
attached a `Handler` to it. A `Logger` with no handlers silently drops every message
regardless of level.

**Fix:** attached both a `FileHandler` (writing to the already-computed `log_file`
path under `results/logs/`) and a `StreamHandler` (console output), both using a
timestamped formatter, and explicitly set `logger.setLevel(logging.DEBUG)` since the
default level would otherwise filter out `.debug()` calls even with handlers present.
Wrapped handler attachment in `if not logger.handlers:` because `logging.getLogger(name)`
returns the same cached instance for a repeated name — without the guard, creating a
second `TestLogger` with the same `test_name` would duplicate every log line.

### 3. `src/testing/test_framework.py` — missing import / empty stub

**Symptom:** the file used `Dict` as a return-type annotation but never imported it
from `typing`; would raise `NameError` on import. `run_test()` was also just `pass`.

**Fix:** implemented the framework fully (see below) with proper imports
(`Dict`, `List`, `Optional` from `typing`).

### 4. `Ammeters/base_ammeter.py` — server crashed on rapid re-runs

**Symptom:** running scripts that start the ammeter emulators back-to-back in quick
succession (e.g. running the test framework multiple times in a row) would
intermittently cause one ammeter's server thread to crash with
`OSError: [Errno 98] Address already in use`, silently killing that server. Every
client request to that ammeter for the rest of the run then failed with
"Connection refused," since nothing was listening on its port anymore.

**Root cause:** the listening socket in `start_server()` didn't set `SO_REUSEADDR`.
After a server closes, the OS can hold that (address, port) pair in a `TIME_WAIT`
state for a short period, during which a new `bind()` to the same port fails unless
the socket explicitly opts in to reusing the address.

**Fix:** added `s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)` before
`bind()`, which allows immediate re-binding even while a prior socket on the same
port is winding down. Verified with 5 back-to-back full test-framework runs after the
fix, versus frequent failures before it.

## Design Decisions

### Unified measurement API

`AmmeterTestFramework._measure_once(ammeter_type)` is the single method that talks to
any configured ammeter: it looks up the port/command from `config.yaml`, opens a
socket, sends the command, and parses the response into a `float`. Adding a new
ammeter type only requires adding an entry to `config.yaml` — no code changes needed,
as long as the new emulator's protocol matches the existing "send command, receive
ASCII float" pattern.

### Sampling

Sample count, sampling frequency, and total duration are all config-driven
(`testing.sampling` in `config.yaml`) rather than hardcoded, so test runs can be
scaled up or down without touching code. When all values are provided, the framework
validates `total_duration_seconds = measurements_count / sampling_frequency_hz` to
avoid ambiguous schedules. It sleeps after every sample, so the complete test window
honors the configured duration.

### Statistics

Used Python's built-in `statistics` module (`mean`, `median`, `stdev`) rather than
numpy, to keep dependencies minimal. `stdev` requires at least 2 data points, so a
single successful sample returns `std_dev: 0.0` rather than raising an exception.

### Result archiving

Chose **JSON files on disk** over SQLite or CSV:
- No new dependencies (`json` is stdlib), consistent with the "minimize dependencies"
  constraint.
- Each run naturally maps to one self-contained file — run ID, timestamps, raw
  samples, computed stats, and any errors all live together, which keeps the format
  human-readable and simple to reload later (`json.load` every file in
  `results/runs/` for historical comparison).
- SQLite would add unnecessary query complexity for what's fundamentally an
  append-only, one-record-per-run use case; CSV struggles to represent nested
  metadata (stats dict, error list) without extra stitching logic.

Filenames combine a timestamp, ammeter type (or `comparison`), and the first 8
characters of the run's UUID, so files sort chronologically and stay unique even
across rapid repeated runs.

### Error handling

Per-sample failures (connection refused, timeout, malformed response) are caught
individually inside the sampling loop rather than aborting the whole run — a single
dropped sample is logged and recorded in the result's `errors` list, but sampling
continues. This means a test run can still produce a partial, useful result (and
accurate statistics on however many samples did succeed) even if the ammeter emulator
is flaky, rather than a single bad connection invalidating an entire N-sample run.

### Cross-ammeter comparison

`run_all_tests()` calls `run_test()` once per configured ammeter and adds a
`comparison` block using **coefficient of variation** (`std_dev / mean`) per ammeter,
rather than a direct numeric comparison of currents.

This was a deliberate choice: Greenlee, ENTES, and CIRCUTOR use different physical
measurement principles (Ohm's Law, Hall Effect, Rogowski Coil) and operate over very
different current ranges (Greenlee/CIRCUTOR readings are typically well under 1A,
while ENTES readings run in the tens-to-hundreds of amps). Comparing their raw values
directly, or picking a "most accurate" one, isn't meaningful without a real
ground-truth reference current — which this emulated setup doesn't provide. Coefficient
of variation instead measures each ammeter's *internal consistency* relative to its
own mean, which is a fair, scale-independent way to say "this ammeter's readings are
comparatively tight or comparatively noisy" without implying one is more *accurate*
than another. The comparison result includes this caveat directly in its output
(`comparison["note"]`) rather than only in this document, so anyone reading the
archived JSON sees the same caveat.

### Visualization (bonus)

Kept plotting in its own module, `src/testing/visualizer.py`, rather than inline in
`AmmeterTestFramework`, for two reasons: it's the only place `matplotlib` is
imported, so the core framework never depends on it unless visualization is actually
enabled; and it keeps a single responsibility per file, which is easier to test and
reuse independently (e.g. re-plotting an old archived JSON result without re-running
a test).

Behavior is entirely config-driven via `analysis.visualization` in `config.yaml`:
- `enabled: true/false` — toggles all plotting on/off.
- `plot_types: [line, histogram]` — controls which plot types `run_test()` generates
  per ammeter. A line chart shows samples in sequence with the run's mean overlaid as
  a reference line; a histogram shows the distribution of the sampled values.

`run_all_tests()` additionally always generates one comparison bar chart (mean
current per ammeter, with std-dev error bars) when visualization is enabled — this
isn't gated by `plot_types` since it's a distinct chart type from the two per-ammeter
plots.

Plots are saved as PNG files under `results/plots/`, named by ammeter type (or
`comparison`) and the run's short UUID, so they're traceable back to the matching
JSON result in `results/runs/`. Plot file paths are also recorded back into the
result dict (`result["plots"]`, `combined["comparison_plot"]`) before archiving, so
the archived JSON always references its own generated images.

Plotting failures are caught and logged rather than allowed to crash a test run —
visualization is a bonus feature, and a plotting bug (e.g. a bad path, a matplotlib
backend issue) shouldn't invalidate an otherwise-successful measurement run. In that
case `result["plots"]` is simply set to an empty list.

### Error simulation (bonus)

Added a config-driven way to inject synthetic failures into the measurement pipeline,
to demonstrate (rather than just assert) that the framework's error handling actually
holds up under fault conditions, not only against real ammeter downtime.

Controlled entirely by `error_simulation` in `config.yaml`:
```yaml
error_simulation:
  enabled: false
  failure_probability: 0.2
  failure_types:
    - connection_refused
    - timeout
    - malformed_data
```
- `enabled` — off by default, so normal test runs and the archived "sample test
  results" deliverable aren't polluted with artificial failures.
- `failure_probability` — chance (0.0-1.0) that any given sample is replaced with a
  simulated fault instead of a real measurement attempt.
- `failure_types` — which fault categories can be injected; a random one is chosen on
  each triggered failure.

Implementation: `AmmeterTestFramework._maybe_simulate_failure()` is called at the top
of `_measure_once()`, before any real socket I/O happens. If triggered, it raises the
same `AmmeterConnectionError` type that a genuine connection problem would raise, with
a `[SIMULATED]` prefix in the message so simulated and real failures stay
distinguishable in logs and archived results. This means the simulation exercises the
*exact* same error-handling path as a real fault — per-sample try/except in
`run_test()`, error logging, appending to the run's `errors` list, and continuing to
collect the remaining samples — rather than a separate, parallel code path that might
not reflect real behavior.

An unrecognized value in `failure_types` (e.g. a config typo) is silently ignored
rather than raising, so a malformed config degrades gracefully instead of crashing an
otherwise-valid test run.

## Known Limitations

- Comparison results (`most_consistent_ammeter`) can vary noticeably between runs
  with the default `measurements_count: 10`, since each emulator generates its
  underlying physical parameters randomly per call. Larger sample sizes would give
  more stable, trustworthy statistics — this is a config change, not a code change.
- The comparison bar chart plots raw mean current for all three ammeters on one
  axis; because ENTES readings run roughly 100-1000x larger than Greenlee/CIRCUTOR,
  the smaller two bars are visually flattened. The chart is still accurate, just not
  ideal for eyeballing the smaller two ammeters side by side — a log-scale y-axis or
  separate subplots would improve this if extended further.
- `error_simulation` should be left `enabled: false` for any run whose results are
  meant to represent real measurement accuracy (e.g. the "sample test results"
  deliverable) — it's a diagnostic/demo feature for exercising error handling, not
  something that should run alongside genuine measurement collection.
