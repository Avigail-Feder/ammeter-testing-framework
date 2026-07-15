import json
import os
import random
import statistics
import time
import uuid
from datetime import datetime
from socket import socket, AF_INET, SOCK_STREAM
from typing import Dict, List, Optional

from ..utils.config import load_config
from ..utils.logger import TestLogger

from ..utils.config import load_config
from ..utils.logger import TestLogger
from . import visualizer


class AmmeterConnectionError(Exception):
    """Raised when a measurement request to an ammeter emulator fails."""
    pass


class AmmeterTestFramework:
    """
    Unified testing framework for the Greenlee, ENTES, and CIRCUTOR ammeter
    emulators. Handles sampling, statistical analysis, and result archiving
    for a single ammeter type, plus a cross-ammeter comparison mode.
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self.logger = TestLogger("ammeter_framework")

        ammeters_cfg = self.config.get("ammeters") or {}
        if not ammeters_cfg:
            raise ValueError(
                "No ammeters configured in config.yaml under 'ammeters:'. "
                "Add at least one ammeter (port + command) before running tests."
            )
        self._ammeters = ammeters_cfg

        sampling_cfg = (self.config.get("testing") or {}).get("sampling") or {}
        self._measurements_count = sampling_cfg.get("measurements_count") or 10
        self._sampling_frequency_hz = sampling_cfg.get("sampling_frequency_hz") or 1

        result_cfg = self.config.get("result_management") or {}
        self._results_dir = result_cfg.get("results_dir", "results/runs")
        os.makedirs(self._results_dir, exist_ok=True)

        viz_cfg = (self.config.get("analysis") or {}).get("visualization") or {}
        self._visualization_enabled = viz_cfg.get("enabled", False)
        self._plot_types = viz_cfg.get("plot_types") or []
        self._plots_dir = os.path.normpath(os.path.join(self._results_dir, "..", "plots"))

        sim_cfg = self.config.get("error_simulation") or {}
        self._simulation_enabled = sim_cfg.get("enabled", False)
        self._failure_probability = sim_cfg.get("failure_probability", 0.0)
        self._failure_types = sim_cfg.get("failure_types") or ["connection_refused", "timeout", "malformed_data"]

    def _maybe_simulate_failure(self, ammeter_type: str, port: int) -> None:
        """
        If error simulation is enabled, roll the configured probability and, on a hit,
        raise a fault mimicking one of `failure_types` -- before any real network I/O
        happens. This exercises the framework's error-handling path (per-sample catch,
        logging, partial-result archiving) on demand without needing to actually break
        an emulator.
        """
        if not self._simulation_enabled or not self._failure_types:
            return
        if random.random() >= self._failure_probability:
            return

        failure_type = random.choice(self._failure_types)

        if failure_type == "connection_refused":
            raise AmmeterConnectionError(
                f"[SIMULATED] Connection refused by {ammeter_type} ammeter on port {port}."
            )
        elif failure_type == "timeout":
            raise AmmeterConnectionError(
                f"[SIMULATED] Timed out waiting for {ammeter_type} ammeter on port {port}."
            )
        elif failure_type == "malformed_data":
            raise AmmeterConnectionError(
                f"[SIMULATED] Received malformed data from {ammeter_type} ammeter on port {port}."
            )
        # Unknown failure_type values are ignored rather than crashing the run --
        # a config typo shouldn't take down an otherwise-working test.

    # ------------------------------------------------------------------ #
    # Unified measurement API
    # ------------------------------------------------------------------ #

    def _measure_once(self, ammeter_type: str, host: str = "localhost", timeout: float = 5.0) -> float:
        """Send a single measurement request to the given ammeter and return the current in Amps."""
        ammeter_cfg = self._ammeters.get(ammeter_type)
        if ammeter_cfg is None:
            raise ValueError(
                f"Unknown ammeter type '{ammeter_type}'. Known types: {list(self._ammeters.keys())}"
            )

        port = ammeter_cfg["port"]
        command = ammeter_cfg["command"].encode("utf-8")

        self._maybe_simulate_failure(ammeter_type, port)

        try:
            with socket(AF_INET, SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect((host, port))
                s.sendall(command)
                data = s.recv(1024)
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            raise AmmeterConnectionError(
                f"Failed to reach {ammeter_type} ammeter on port {port}: {exc}"
            ) from exc

        if not data:
            raise AmmeterConnectionError(
                f"No data received from {ammeter_type} ammeter on port {port}."
            )

        try:
            return float(data.decode("utf-8"))
        except ValueError as exc:
            raise AmmeterConnectionError(
                f"Received malformed data from {ammeter_type} ammeter: {data!r}"
            ) from exc

    # ------------------------------------------------------------------ #
    # Sampling + single-ammeter test run
    # ------------------------------------------------------------------ #

    def run_test(self, ammeter_type: str) -> Dict:
        """
        Sample `measurements_count` current readings from the given ammeter type,
        at the configured sampling frequency, compute summary statistics, archive
        the run to disk, and return the full result as a dict.
        """
        ammeter_type = ammeter_type.lower()
        run_id = str(uuid.uuid4())
        started_at = datetime.now().isoformat()
        interval = (1.0 / self._sampling_frequency_hz) if self._sampling_frequency_hz else 0

        samples: List[float] = []
        errors: List[str] = []

        self.logger.info(f"Starting test run {run_id} for ammeter '{ammeter_type}'")

        for i in range(self._measurements_count):
            try:
                value = self._measure_once(ammeter_type)
                samples.append(value)
                self.logger.debug(
                    f"[{ammeter_type}] sample {i + 1}/{self._measurements_count}: {value} A"
                )
            except (AmmeterConnectionError, ValueError) as exc:
                errors.append(str(exc))
                self.logger.error(f"[{ammeter_type}] sample {i + 1} failed: {exc}")

            if interval and i < self._measurements_count - 1:
                time.sleep(interval)

        stats = self._compute_statistics(samples)

        result = {
            "run_id": run_id,
            "ammeter_type": ammeter_type,
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(),
            "requested_measurements": self._measurements_count,
            "successful_measurements": len(samples),
            "failed_measurements": len(errors),
            "samples": samples,
            "errors": errors,
            "statistics": stats,
        }

        if self._visualization_enabled and self._plot_types:
            try:
                plot_paths = visualizer.plot_single_result(result, self._plot_types, self._plots_dir)
                result["plots"] = plot_paths
                for p in plot_paths:
                    self.logger.info(f"Saved plot: {p}")
            except Exception as exc:
                # Plotting is a bonus feature -- never let it break a test run.
                self.logger.error(f"Visualization failed for '{ammeter_type}': {exc}")
                result["plots"] = []

        self._archive_result(result)
        self.logger.info(
            f"Finished test run {run_id} for '{ammeter_type}': "
            f"{len(samples)}/{self._measurements_count} successful"
        )
        return result

    # ------------------------------------------------------------------ #
    # Cross-ammeter comparison (bonus: accuracy assessment)
    # ------------------------------------------------------------------ #

    def run_all_tests(self) -> Dict:
        """
        Run `run_test` for every ammeter type in the config and return a combined
        dict, including a cross-ammeter comparison, suitable for archiving.
        """
        results = {}
        for ammeter_type in self._ammeters:
            results[ammeter_type] = self.run_test(ammeter_type)

        comparison = self._compare_results(results)

        combined = {
            "run_id": str(uuid.uuid4()),
            "generated_at": datetime.now().isoformat(),
            "results": results,
            "comparison": comparison,
        }
        if self._visualization_enabled:
            try:
                plot_path = visualizer.plot_comparison(combined, self._plots_dir)
                combined["comparison_plot"] = plot_path
                if plot_path:
                    self.logger.info(f"Saved comparison plot: {plot_path}")
            except Exception as exc:
                self.logger.error(f"Comparison visualization failed: {exc}")
                combined["comparison_plot"] = None
        self._archive_result(combined, prefix="comparison")
        return combined

    @staticmethod
    def _compute_statistics(samples: List[float]) -> Dict:
        if not samples:
            return {"mean": None, "median": None, "std_dev": None, "min": None, "max": None}

        return {
            "mean": statistics.mean(samples),
            "median": statistics.median(samples),
            "std_dev": statistics.stdev(samples) if len(samples) > 1 else 0.0,
            "min": min(samples),
            "max": max(samples),
        }

    @staticmethod
    def _compare_results(results: Dict) -> Dict:
        """
        Compare mean current and relative variability (coefficient of variation)
        across ammeter types. Lower CoV => tighter, more internally-consistent readings.
        """
        comparison = {}
        cov_by_ammeter: Dict[str, float] = {}

        for ammeter_type, result in results.items():
            stats = result["statistics"]
            mean = stats["mean"]
            std_dev = stats["std_dev"]
            cov = (std_dev / mean) if mean else None

            comparison[ammeter_type] = {
                "mean_current": mean,
                "std_dev": std_dev,
                "coefficient_of_variation": cov,
            }
            if cov is not None:
                cov_by_ammeter[ammeter_type] = cov

        most_consistent = min(cov_by_ammeter, key=cov_by_ammeter.get) if cov_by_ammeter else None

        return {
            "per_ammeter": comparison,
            "most_consistent_ammeter": most_consistent,
            "note": (
                "Ammeter types use different measurement principles and value ranges "
                "(Ohm's Law, Hall Effect, Rogowski Coil), so raw current readings are not "
                "directly comparable to each other. Coefficient of variation (std_dev / mean) "
                "is used as a proxy for measurement consistency rather than absolute accuracy, "
                "since this emulated setup has no ground-truth reference current to compare against."
            ),
        }

    # ------------------------------------------------------------------ #
    # Result archiving
    # ------------------------------------------------------------------ #

    def _archive_result(self, result: Dict, prefix: Optional[str] = None) -> str:
        """Save a result dict to a timestamped JSON file under results_dir. Returns the file path."""
        prefix = prefix or result.get("ammeter_type", "run")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id_short = result["run_id"][:8]
        filename = f"{timestamp}_{prefix}_{run_id_short}.json"
        filepath = os.path.join(self._results_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        self.logger.info(f"Archived result to {filepath}")
        return filepath