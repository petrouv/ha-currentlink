"""Data models and state structures for TP-Link PG2400P."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import overload

from .const import RATE_SCALE, ZERO_MAC

_LOGGER = logging.getLogger(__name__)

MAX_UINT32: int = 1 << 32

PORT_SPEED_MAP: dict[int, str] = {
    0: "Disconnected",
    1: "10M Half",
    2: "10M Full",
    3: "100M Half",
    4: "100M Full",
    5: "1000M Half",
    6: "1000M Full",
}

PHY_MODE_MAP: dict[str, str] = {
    "7": "MIMO",
    "23": "SISO",
}

COMPATIBILITY_MODE_MAP: dict[str, str] = {
    "1": "Full Power",
    "2": "VDSL 17a",
    "3": "VDSL 35b",
}

QOS_MODE_MAP: dict[str, str] = {
    "1": "Fair",
    "2": "Gaming",
    "3": "Streaming",
    "4": "Voice",
}

PHY_MODE_REVERSE_MAP: dict[str, str] = {v: k for k, v in PHY_MODE_MAP.items()}
COMPATIBILITY_MODE_REVERSE_MAP: dict[str, str] = {v: k for k, v in COMPATIBILITY_MODE_MAP.items()}
QOS_MODE_REVERSE_MAP: dict[str, str] = {v: k for k, v in QOS_MODE_MAP.items()}


@dataclass(frozen=True, kw_only=True)
class PowerlineSettings:
    """Static radio parameters and adapter profiles."""

    phy_mode: str = "Unknown"
    compatibility_mode: str = "Unknown"
    qos_mode: str = "Unknown"
    xdsl_mitigation: bool = False


class PortSpeedCode(IntEnum):
    """Ethernet physical port speed codes."""

    DISCONNECTED = 0
    SPEED_10M_HALF = 1
    SPEED_10M_FULL = 2
    SPEED_100M_HALF = 3
    SPEED_100M_FULL = 4
    SPEED_1000M_HALF = 5
    SPEED_1000M_FULL = 6


@dataclass(frozen=True, kw_only=True)
class PortStatus:
    """Status of physical RJ45 Ethernet ports."""

    port1_code: int = 0
    port1_speed: str = "Disconnected"
    port2_code: int = 0
    port2_speed: str = "Disconnected"

    @classmethod
    def from_codes(cls, port1: int, port2: int) -> PortStatus:
        """Create PortStatus from firmware numeric status codes."""
        return cls(
            port1_code=port1,
            port1_speed=PORT_SPEED_MAP.get(port1, f"Unknown ({port1})"),
            port2_code=port2,
            port2_speed=PORT_SPEED_MAP.get(port2, f"Unknown ({port2})"),
        )


@dataclass(frozen=True, kw_only=True)
class DeviceInfo:
    """Identification and network parameters of TP-Link PG2400P adapter."""

    model: str = "PG2400P"
    hw_revision: str
    fw_version: str
    mac_address: str
    device_name: str
    domain_name: str
    ip_address: str
    lan_power_saving: bool = False
    traffic_power_saving: bool = False


@dataclass(frozen=True, kw_only=True)
class PeerLink:
    """Connection parameters for a remote Powerline peer."""

    peer_mac: str
    rx_rate_mbps: float | int
    tx_rate_mbps: float | int
    rx_raw: int = 0
    tx_raw: int = 0
    active: bool = True

    @property
    def rx_raw_bps(self) -> int:
        """Raw receive rate in BPS."""
        return self.rx_raw

    @property
    def tx_raw_bps(self) -> int:
        """Raw transmit rate in BPS."""
        return self.tx_raw


@dataclass(frozen=True, kw_only=True)
class TelemetrySnapshot:
    """Instantaneous snapshot of physical layer telemetry and G.hn counters."""

    timestamp: float
    uptime_seconds: float
    attenuation_db: float | None
    wire_length_m: int | None
    xput_indicator_mbps: int
    g9962_counters: dict[str, int]
    channel_adaptations: dict[str, int]
    eth_stats: dict[str, int]
    master_losses: int
    qos_discarded_pkts: int = 0
    llc_errors: dict[str, int] = field(default_factory=dict)
    raw_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class TelemetryInterval:
    """Calculated interval metrics across a polling period."""

    duration_seconds: float
    tx_bytes: int
    rx_bytes: int
    throughput_tx_mbps: float
    throughput_rx_mbps: float
    retransmission_rate_pct: float | None
    receive_bler_pct: float | None
    blocks_tx: int
    blocks_rx: int
    blocks_rtx: int
    blocks_error_rx: int
    g9962_deltas: dict[str, int] = field(default_factory=dict)
    master_losses_delta: int = 0


@dataclass(frozen=True, kw_only=True)
class PG2400PData:
    """Aggregated adapter state for DataUpdateCoordinator."""

    device_info: DeviceInfo
    peers: list[PeerLink]
    port_status: PortStatus | None = None
    telemetry: TelemetrySnapshot
    telemetry_interval: TelemetryInterval | None = None
    led_enabled: bool | None = None
    settings: PowerlineSettings | None = None

    @property
    def plc_link_active(self) -> bool:
        """Indicator whether at least one remote peer connection is active."""
        return any(peer.active for peer in self.peers) if self.peers else False


def compute_counter_delta(curr_val: int, prev_val: int) -> int | None:
    """Calculate counter delta with 32-bit rollover handling.

    Args:
        curr_val: Current counter value.
        prev_val: Previous counter value.

    Returns:
        Counter delta or None if an invalid counter reset occurred.
    """
    if curr_val >= prev_val:
        return curr_val - prev_val
    # 32-bit rollover without reboot
    wrapped = curr_val + MAX_UINT32 - prev_val
    if wrapped < MAX_UINT32:
        return wrapped
    return None


def compute_telemetry_interval(
    prev: TelemetrySnapshot,
    curr: TelemetrySnapshot,
) -> TelemetryInterval | None:
    """Calculate interval metrics between two consecutive telemetry snapshots.

    Resilient to adapter reboots and 32-bit hardware counter rollover
    on Xtensa SoC.

    Args:
        prev: Previous telemetry snapshot.
        curr: Current telemetry snapshot.

    Returns:
        TelemetryInterval with calculated throughput and BLER, or None
        on reset/reboot or non-positive duration.
    """
    # 1. Detect adapter reboot via monotonic uptime
    if curr.uptime_seconds < prev.uptime_seconds:
        _LOGGER.info(
            "Detected TP-Link PG2400P reboot (uptime %.1f < %.1f s). Resetting baseline.",
            curr.uptime_seconds,
            prev.uptime_seconds,
        )
        return None

    # 2. Determine interval duration
    uptime_delta = curr.uptime_seconds - prev.uptime_seconds
    duration = uptime_delta if 0.5 <= uptime_delta <= 3600.0 else (curr.timestamp - prev.timestamp)

    if duration <= 0:
        _LOGGER.debug("Telemetry interval <= 0 seconds. Skipping delta calculation.")
        return None

    # 3. Compute G.9962 counter deltas
    deltas: dict[str, int] = {}
    for key, curr_val in curr.g9962_counters.items():
        prev_val = prev.g9962_counters.get(key)
        if prev_val is None:
            continue
        d = compute_counter_delta(curr_val, prev_val)
        if d is None:
            _LOGGER.debug("Counter %s decreased without indication of reboot.", key)
            return None
        deltas[key] = d

    tx_bytes = deltas.get("Bytes_TX", 0)
    rx_bytes = deltas.get("Bytes_RX", 0)
    blocks_tx = deltas.get("BLOCKS_TX", 0)
    blocks_rx = deltas.get("BLOCKS_RX", 0)
    blocks_rtx = deltas.get("BLOCKS_RTX", 0)
    blocks_error_rx = deltas.get("BLOCKS_ERROR_RX", 0)

    # 4. Calculate payload throughput (Mbit/s)
    throughput_tx_mbps = (tx_bytes * 8.0) / (duration * 1_000_000.0)
    throughput_rx_mbps = (rx_bytes * 8.0) / (duration * 1_000_000.0)

    # 5. Calculate error and retransmission percentages
    retransmission_rate_pct = (blocks_rtx / blocks_tx * 100.0) if blocks_tx > 0 else 0.0
    receive_bler_pct = (blocks_error_rx / blocks_rx * 100.0) if blocks_rx > 0 else 0.0

    master_losses_delta = max(0, curr.master_losses - prev.master_losses)

    return TelemetryInterval(
        duration_seconds=round(duration, 4),
        tx_bytes=tx_bytes,
        rx_bytes=rx_bytes,
        throughput_tx_mbps=round(throughput_tx_mbps, 4),
        throughput_rx_mbps=round(throughput_rx_mbps, 4),
        retransmission_rate_pct=round(retransmission_rate_pct, 4),
        receive_bler_pct=round(receive_bler_pct, 4),
        blocks_tx=blocks_tx,
        blocks_rx=blocks_rx,
        blocks_rtx=blocks_rtx,
        blocks_error_rx=blocks_error_rx,
        g9962_deltas=deltas,
        master_losses_delta=master_losses_delta,
    )


def _parse_csv_integers(raw_str: str) -> list[int]:
    """Parse comma-separated values string into a list of integers."""
    if not raw_str:
        return []
    result: list[int] = []
    for item in raw_str.split(","):
        stripped = item.strip()
        if stripped:
            with contextlib.suppress(ValueError):
                result.append(int(stripped))
    return result


def _parse_csv_strings(raw_str: str) -> list[str]:
    """Parse comma-separated values string into a list of strings."""
    if not raw_str:
        return []
    return [item.strip() for item in raw_str.split(",") if item.strip()]


def _map_series(desc_str: str, val_str: str) -> dict[str, int]:
    """Map metric names to their numeric values."""
    names = _parse_csv_strings(desc_str)
    values = _parse_csv_integers(val_str)
    result: dict[str, int] = {}
    for idx, val in enumerate(values):
        if idx < len(names):
            result[names[idx]] = val
        else:
            result[f"UNMAPPED_{idx - len(names) + 1}"] = val
    return result


@overload
def decode_telemetry_snapshot(
    fields: Mapping[str, str],
    *,
    host: str = "",
    timestamp: float | None = None,
) -> TelemetrySnapshot: ...


@overload
def decode_telemetry_snapshot(
    host: str,
    fields: Mapping[str, str],
    *,
    timestamp: float | None = None,
) -> TelemetrySnapshot: ...


def decode_telemetry_snapshot(
    fields_or_host: Mapping[str, str] | str,
    fields: Mapping[str, str] | None = None,
    *,
    host: str = "",
    timestamp: float | None = None,
) -> TelemetrySnapshot:
    """Decode 20 adapter response fields into a TelemetrySnapshot structure.

    Supports invocations as decode_telemetry_snapshot(host, fields)
    and decode_telemetry_snapshot(fields).

    Args:
        fields_or_host: Mapping of telemetry fields or host string.
        fields: Mapping of telemetry fields (if host was passed as first arg).
        host: Hostname or IP of the adapter.
        timestamp: Timestamp (defaults to time.time()).

    Returns:
        TelemetrySnapshot object populated with parsed fields.
    """
    if isinstance(fields_or_host, Mapping):
        raw_fields = dict(fields_or_host)
    elif fields is not None:
        raw_fields = dict(fields)
        if not host:
            host = fields_or_host
    else:
        raw_fields = {}

    current_ts = time.time() if timestamp is None else timestamp

    # 1. Adapter uptime (MSEC from QOS.STATS.INFO)
    qos_info_raw = raw_fields.get("QOS.STATS.INFO", "")
    qos_info_vals = _parse_csv_integers(qos_info_raw)
    uptime_seconds = qos_info_vals[1] / 1000.0 if len(qos_info_vals) > 1 else current_ts
    qos_discarded_pkts = qos_info_vals[2] if len(qos_info_vals) > 2 else 0

    # 2. Attenuation (DIDMNG.GENERAL.AVG_ATTENUATION)
    atten_vals = _parse_csv_integers(raw_fields.get("DIDMNG.GENERAL.AVG_ATTENUATION", ""))
    peer_atten_raw = (
        atten_vals[2] if len(atten_vals) >= 3 else (atten_vals[-1] if atten_vals else 0)
    )
    attenuation_db: float | None
    if peer_atten_raw <= 0 or peer_atten_raw >= 2000:
        attenuation_db = None
    else:
        attenuation_db = round(peer_atten_raw / 10.0, 1)

    # 3. Wire length (DIDMNG.GENERAL.WIRE_LENGTH)
    wire_vals = _parse_csv_integers(raw_fields.get("DIDMNG.GENERAL.WIRE_LENGTH", ""))
    peer_wire_raw = wire_vals[2] if len(wire_vals) >= 3 else (wire_vals[-1] if wire_vals else 0)
    wire_length_m: int | None = peer_wire_raw if peer_wire_raw > 0 else None

    # 4. Application throughput (FLOWMONITOR.INFO.XPUT_INDICATOR)
    xput_raw = raw_fields.get("FLOWMONITOR.INFO.XPUT_INDICATOR", "0").strip()
    try:
        xput_indicator_mbps = int(xput_raw)
    except ValueError:
        xput_indicator_mbps = 0

    # 5. G.9962 counters
    g9962_counters = _map_series(
        raw_fields.get("QOS.STATS.G9962_DESC", ""),
        raw_fields.get("QOS.STATS.G9962", ""),
    )

    # 6. Channel adaptation
    channel_adaptations = _map_series(
        raw_fields.get("QOS.STATS.CHANNEL_INFO_DESC", ""),
        raw_fields.get("QOS.STATS.CHANNEL_INFO", ""),
    )

    # 7. Ethernet statistics
    eth_stats = _map_series(
        raw_fields.get("ETHIFDRIVER.STATS.INFO_DESC", ""),
        raw_fields.get("ETHIFDRIVER.STATS.INFO", ""),
    )

    # 8. LLC RX errors (CRC and cipher MIC errors)
    llc_errors = _map_series(
        raw_fields.get("QOS.STATS.RX_LLC_ERRORS_DESC", ""),
        raw_fields.get("QOS.STATS.RX_LLC_ERRORS", ""),
    )

    # 9. Master Domain losses
    master_raw = raw_fields.get("MASTERSELECTION.STATS.INFO", "")
    master_vals = _parse_csv_integers(master_raw)
    master_losses = master_vals[2] if len(master_vals) > 2 else 0

    return TelemetrySnapshot(
        timestamp=current_ts,
        uptime_seconds=uptime_seconds,
        attenuation_db=attenuation_db,
        wire_length_m=wire_length_m,
        xput_indicator_mbps=xput_indicator_mbps,
        g9962_counters=g9962_counters,
        channel_adaptations=channel_adaptations,
        eth_stats=eth_stats,
        master_losses=master_losses,
        qos_discarded_pkts=qos_discarded_pkts,
        llc_errors=llc_errors,
        raw_fields=raw_fields,
    )


__all__ = [
    "COMPATIBILITY_MODE_MAP",
    "COMPATIBILITY_MODE_REVERSE_MAP",
    "DeviceInfo",
    "MAX_UINT32",
    "PHY_MODE_MAP",
    "PHY_MODE_REVERSE_MAP",
    "PG2400PData",
    "PORT_SPEED_MAP",
    "PeerLink",
    "PortSpeedCode",
    "PortStatus",
    "PowerlineSettings",
    "QOS_MODE_MAP",
    "QOS_MODE_REVERSE_MAP",
    "RATE_SCALE",
    "TelemetryInterval",
    "TelemetrySnapshot",
    "ZERO_MAC",
    "compute_counter_delta",
    "compute_telemetry_interval",
    "decode_telemetry_snapshot",
]
