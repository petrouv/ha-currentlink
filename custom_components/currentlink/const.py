"""Constants for the TP-Link PG2400P integration."""

from __future__ import annotations

from typing import Final

try:
    from homeassistant.const import (
        CONF_HOST,
        CONF_PASSWORD,
        CONF_SCAN_INTERVAL,
        Platform,
    )
except ImportError:  # pragma: no cover
    # Fallback for standalone testing without Home Assistant installed
    from enum import StrEnum

    CONF_HOST = "host"
    CONF_PASSWORD = "password"
    CONF_SCAN_INTERVAL = "scan_interval"

    class Platform(StrEnum):
        """Home Assistant platforms."""

        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        SWITCH = "switch"
        BUTTON = "button"
        SELECT = "select"


DOMAIN: Final = "currentlink"
MANUFACTURER: Final = "TP-Link"
MODEL: Final = "PG2400P"
DEFAULT_NAME: Final = "TP-Link PG2400P"

# Default values and limits
DEFAULT_SCAN_INTERVAL: Final = 30  # Polling interval in seconds
DEFAULT_TIMEOUT: Final = 10.0  # Network request timeout in seconds
MIN_SCAN_INTERVAL: Final = 15  # Minimum allowed polling interval (15s)
MAX_SCAN_INTERVAL: Final = 300  # Maximum allowed polling interval (300s)
DEFAULT_PORT: Final = 80  # Default HTTP port of web interface

# Supported entity platforms
PLATFORMS: Final[list[Platform]] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SELECT,
]

# Home Assistant device registry and attribute keys
ATTR_IDENTIFIERS: Final = "identifiers"
ATTR_MANUFACTURER: Final = "manufacturer"
ATTR_MODEL: Final = "model"
ATTR_SW_VERSION: Final = "sw_version"
ATTR_HW_VERSION: Final = "hw_version"
ATTR_CONNECTIONS: Final = "connections"
ATTR_MAC_ADDRESS: Final = "mac_address"
ATTR_IP_ADDRESS: Final = "ip_address"
ATTR_ATTENUATION_DB: Final = "attenuation_db"
ATTR_WIRE_LENGTH_M: Final = "wire_length_m"
ATTR_RX_RATE_MBPS: Final = "rx_rate_mbps"
ATTR_TX_RATE_MBPS: Final = "tx_rate_mbps"
ATTR_THROUGHPUT_MBPS: Final = "throughput_mbps"
ATTR_BLER_PCT: Final = "bler_pct"
ATTR_RETRANSMISSION_RATE_PCT: Final = "retransmission_rate_pct"
ATTR_ACTIVE_PEERS: Final = "active_peers"

# Telemetry helper constants
RATE_SCALE: Final = 32  # Divisor to convert raw BPS to Mbit/s
ZERO_MAC: Final = "00:00:00:00:00:00"  # Reserved zero MAC address

__all__ = [
    "ATTR_ACTIVE_PEERS",
    "ATTR_ATTENUATION_DB",
    "ATTR_CONNECTIONS",
    "ATTR_HW_VERSION",
    "ATTR_IDENTIFIERS",
    "ATTR_IP_ADDRESS",
    "ATTR_MAC_ADDRESS",
    "ATTR_MANUFACTURER",
    "ATTR_MODEL",
    "ATTR_RETRANSMISSION_RATE_PCT",
    "ATTR_RX_RATE_MBPS",
    "ATTR_SW_VERSION",
    "ATTR_THROUGHPUT_MBPS",
    "ATTR_TX_RATE_MBPS",
    "ATTR_WIRE_LENGTH_M",
    "CONF_HOST",
    "CONF_PASSWORD",
    "CONF_SCAN_INTERVAL",
    "DEFAULT_NAME",
    "DEFAULT_PORT",
    "DEFAULT_SCAN_INTERVAL",
    "DEFAULT_TIMEOUT",
    "DOMAIN",
    "MANUFACTURER",
    "MAX_SCAN_INTERVAL",
    "MIN_SCAN_INTERVAL",
    "MODEL",
    "PLATFORMS",
    "Platform",
    "RATE_SCALE",
    "ZERO_MAC",
]
