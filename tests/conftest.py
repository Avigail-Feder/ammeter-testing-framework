"""Shared fixtures for the ammeter framework test suite."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import yaml

from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from src.testing.test_framework import AmmeterTestFramework


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _start_server(ammeter_class, port: int) -> None:
    thread = threading.Thread(
        target=lambda: ammeter_class(port).start_server(), daemon=True
    )
    thread.start()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.01)
    pytest.fail(f"{ammeter_class.__name__} did not start on port {port}")


@pytest.fixture
def framework(tmp_path: Path) -> AmmeterTestFramework:
    """A framework connected to real emulator servers on test-only ports."""
    ports = {name: _free_port() for name in ("greenlee", "entes", "circutor")}
    for cls, name in (
        (GreenleeAmmeter, "greenlee"),
        (EntesAmmeter, "entes"),
        (CircutorAmmeter, "circutor"),
    ):
        _start_server(cls, ports[name])

    config = {
        "testing": {"sampling": {"measurements_count": 1, "sampling_frequency_hz": 0}},
        "ammeters": {
            "greenlee": {"port": ports["greenlee"], "command": "MEASURE_GREENLEE -get_measurement"},
            "entes": {"port": ports["entes"], "command": "MEASURE_ENTES -get_data"},
            "circutor": {"port": ports["circutor"], "command": "MEASURE_CIRCUTOR -get_measurement -current"},
        },
        "analysis": {"visualization": {"enabled": False}},
        "result_management": {"results_dir": str(tmp_path / "runs")},
        "error_simulation": {"enabled": False},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return AmmeterTestFramework(str(config_path))
