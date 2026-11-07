"""Общие фикстуры и настройки для набора тестов TP-Link PG2400P."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Добавление корня проекта и custom_components в sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = PROJECT_ROOT / "custom_components" / "currentlink"

for p in (str(PROJECT_ROOT), str(COMPONENT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from custom_components.currentlink.models import (  # noqa: E402
    TelemetrySnapshot,
    decode_telemetry_snapshot,
)
from custom_components.currentlink.protocol import parse_response  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Разрешает загрузку пользовательских компонентов в Home Assistant."""
    yield


@pytest.fixture
def sample_telemetry_1_text() -> str:
    """Текст первого снимка телеметрии адаптера."""
    path = FIXTURES_DIR / "telemetry-sample-1.txt"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def sample_telemetry_2_text() -> str:
    """Текст второго снимка телеметрии (спустя 89.96 с)."""
    path = FIXTURES_DIR / "telemetry-sample-2.txt"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def sample_telemetry_1_dict(sample_telemetry_1_text: str) -> dict[str, str]:
    """Словарь полей телеметрии 1, полученный через parse_response."""
    full_text = f"ERROR=000\r\n{sample_telemetry_1_text}"
    _, data = parse_response(full_text)
    return data


@pytest.fixture
def sample_telemetry_2_dict(sample_telemetry_2_text: str) -> dict[str, str]:
    """Словарь полей телеметрии 2, полученный через parse_response."""
    full_text = f"ERROR=000\r\n{sample_telemetry_2_text}"
    _, data = parse_response(full_text)
    return data


@pytest.fixture
def sample_snapshot_1(sample_telemetry_1_dict: dict[str, str]) -> TelemetrySnapshot:
    """Объект TelemetrySnapshot для первого снимка."""
    return decode_telemetry_snapshot(sample_telemetry_1_dict, host="192.0.2.10", timestamp=1000.0)


@pytest.fixture
def sample_snapshot_2(sample_telemetry_2_dict: dict[str, str]) -> TelemetrySnapshot:
    """Объект TelemetrySnapshot для второго снимка."""
    return decode_telemetry_snapshot(sample_telemetry_2_dict, host="192.0.2.10", timestamp=1089.96)
