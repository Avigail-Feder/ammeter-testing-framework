"""Unit and integration tests for the public ammeter test framework API."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time

import pytest

from src.testing.test_framework import AmmeterConnectionError, AmmeterTestFramework
from src.testing import test_framework


def test_compute_statistics_for_multiple_samples() -> None:
    stats = AmmeterTestFramework._compute_statistics([1.0, 2.0, 3.0])

    assert stats == {"mean": 2.0, "median": 2.0, "std_dev": 1.0, "min": 1.0, "max": 3.0}


def test_compute_statistics_for_empty_and_single_sample() -> None:
    assert AmmeterTestFramework._compute_statistics([]) == {
        "mean": None, "median": None, "std_dev": None, "min": None, "max": None
    }
    assert AmmeterTestFramework._compute_statistics([4.2])["std_dev"] == 0.0


@pytest.mark.parametrize("ammeter_type", ["greenlee", "entes", "circutor"])
def test_measure_once_uses_each_real_emulator(framework, ammeter_type: str) -> None:
    value = framework._measure_once(ammeter_type, timeout=1)

    assert isinstance(value, float)
    assert value > 0


def test_unknown_ammeter_is_rejected(framework) -> None:
    with pytest.raises(ValueError, match="Unknown ammeter type"):
        framework._measure_once("not-a-meter")


def test_invalid_command_returns_a_framework_error(framework) -> None:
    framework._ammeters["greenlee"]["command"] = "INVALID"

    with pytest.raises(AmmeterConnectionError, match="malformed data"):
        framework._measure_once("greenlee", timeout=1)


def test_timeout_is_reported_as_connection_error(framework) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("localhost", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def accept_without_reply() -> None:
        with listener:
            connection, _ = listener.accept()
            with connection:
                time.sleep(0.1)

    threading.Thread(target=accept_without_reply, daemon=True).start()
    framework._ammeters["greenlee"]["port"] = port

    with pytest.raises(AmmeterConnectionError, match="Failed to reach"):
        framework._measure_once("greenlee", timeout=0.01)


def test_run_test_records_successes_errors_and_archive(framework, monkeypatch) -> None:
    framework._measurements_count = 3
    framework._sampling_frequency_hz = 0  # Keep this unit test fast; timing is tested separately.
    measurements = iter([1.0, AmmeterConnectionError("temporary failure"), 3.0])

    def measure_once(_ammeter_type: str) -> float:
        outcome = next(measurements)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(framework, "_measure_once", measure_once)
    result = framework.run_test("greenlee")

    assert result["successful_measurements"] == 2
    assert result["failed_measurements"] == 1
    assert result["samples"] == [1.0, 3.0]
    assert result["statistics"]["mean"] == 2.0

    archived = list(Path(framework._results_dir).glob("*.json"))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text(encoding="utf-8"))["run_id"] == result["run_id"]


def test_run_test_honours_configured_sampling_duration(framework, monkeypatch) -> None:
    framework._measurements_count = 3
    framework._sampling_frequency_hz = 2
    framework._total_duration_seconds = 1.5
    monkeypatch.setattr(framework, "_measure_once", lambda _ammeter_type: 1.0)
    sleep_calls = []
    monkeypatch.setattr(test_framework.time, "sleep", sleep_calls.append)

    result = framework.run_test("greenlee")

    assert sleep_calls == [0.5, 0.5, 0.5]
    assert result["duration_seconds"] == 1.5


def test_compare_results_chooses_lowest_coefficient_of_variation() -> None:
    results = {
        "steady": {"statistics": {"mean": 10.0, "std_dev": 1.0}},
        "noisy": {"statistics": {"mean": 10.0, "std_dev": 5.0}},
    }

    comparison = AmmeterTestFramework._compare_results(results)

    assert comparison["most_consistent_ammeter"] == "steady"
    assert comparison["per_ammeter"]["steady"]["coefficient_of_variation"] == 0.1
