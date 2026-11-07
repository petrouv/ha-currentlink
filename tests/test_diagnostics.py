"""Unit tests for TP-Link PG2400P diagnostics support."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.const import DOMAIN
from custom_components.currentlink.diagnostics import async_get_config_entry_diagnostics
from custom_components.currentlink.models import (
    DeviceInfo,
    PeerLink,
    PG2400PData,
    PortStatus,
    PowerlineSettings,
    TelemetryInterval,
    TelemetrySnapshot,
)


def _build_sample_data() -> PG2400PData:
    """Build sample PG2400PData for testing diagnostics."""
    return PG2400PData(
        device_info=DeviceInfo(
            model="PG2400P",
            hw_revision="1.0",
            fw_version="1.1.0 Build 20250710 Rel.56841",
            mac_address="aa:bb:cc:dd:ee:ff",
            device_name="TP-Link PG2400P",
            domain_name="HomeNetwork",
            ip_address="192.0.2.10",
            lan_power_saving=False,
            traffic_power_saving=False,
        ),
        peers=[
            PeerLink(
                peer_mac="11:22:33:44:55:66",
                rx_rate_mbps=1200,
                tx_rate_mbps=950,
                rx_raw=38400,
                tx_raw=30400,
                active=True,
            )
        ],
        port_status=PortStatus.from_codes(6, 4),
        telemetry=TelemetrySnapshot(
            timestamp=1700000000.0,
            uptime_seconds=86400.0,
            attenuation_db=18.5,
            wire_length_m=25,
            xput_indicator_mbps=750,
            g9962_counters={"Bytes_TX": 1000000, "Bytes_RX": 2000000},
            channel_adaptations={},
            eth_stats={},
            master_losses=0,
            qos_discarded_pkts=0,
            raw_fields={"TCPIP.IPV4.IP_ADDRESS": "192.0.2.10"},
        ),
        telemetry_interval=TelemetryInterval(
            duration_seconds=30.0,
            tx_bytes=10000,
            rx_bytes=20000,
            throughput_tx_mbps=2.67,
            throughput_rx_mbps=5.33,
            retransmission_rate_pct=0.15,
            receive_bler_pct=0.01,
            blocks_tx=500,
            blocks_rx=1000,
            blocks_rtx=1,
            blocks_error_rx=0,
        ),
        led_enabled=True,
        settings=PowerlineSettings(
            phy_mode="MIMO",
            compatibility_mode="Full Power",
            qos_mode="Fair",
            xdsl_mitigation=False,
        ),
    )


async def test_diagnostics_redacts_sensitive_data(hass: HomeAssistant) -> None:
    """Test diagnostics redacts passwords, host, IPs and MAC addresses."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TP-Link PG2400P (Test)",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PASSWORD: "SuperSecretPassword123!",
            CONF_SCAN_INTERVAL: 30,
        },
        entry_id="mock_entry_123",
    )
    entry.add_to_hass(hass)

    mock_coordinator = MagicMock()
    mock_coordinator.data = _build_sample_data()
    entry.runtime_data = mock_coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "entry" in diag
    assert "data" in diag

    # Verify entry redaction
    entry_data = diag["entry"]["data"]
    assert entry_data[CONF_PASSWORD] == REDACTED
    assert entry_data[CONF_HOST] == REDACTED
    assert entry_data[CONF_SCAN_INTERVAL] == 30

    # Verify coordinator data redaction
    coordinator_data = diag["data"]
    dev_info = coordinator_data["device_info"]
    assert dev_info["mac_address"] == REDACTED
    assert dev_info["ip_address"] == REDACTED
    assert dev_info["model"] == "PG2400P"
    assert dev_info["fw_version"] == "1.1.0 Build 20250710 Rel.56841"

    # Verify peer MAC redaction
    peer_info = coordinator_data["peers"][0]
    assert peer_info["peer_mac"] == REDACTED
    assert peer_info["rx_rate_mbps"] == 1200
    assert peer_info["tx_rate_mbps"] == 950

    # Verify raw fields redaction
    raw_fields = coordinator_data["telemetry"]["raw_fields"]
    assert raw_fields["TCPIP.IPV4.IP_ADDRESS"] == REDACTED

    # Verify non-sensitive telemetry remains intact
    assert coordinator_data["telemetry"]["attenuation_db"] == 18.5
    assert coordinator_data["telemetry"]["wire_length_m"] == 25
    assert coordinator_data["port_status"]["port1_speed"] == "1000M Full"
    assert coordinator_data["settings"]["phy_mode"] == "MIMO"


async def test_diagnostics_when_coordinator_data_is_none(hass: HomeAssistant) -> None:
    """Test diagnostics behaves safely when coordinator data is None."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TP-Link PG2400P (Empty)",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PASSWORD: "SecretPassword",
        },
        entry_id="mock_entry_empty",
    )
    entry.add_to_hass(hass)

    mock_coordinator = MagicMock()
    mock_coordinator.data = None
    entry.runtime_data = mock_coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "entry" in diag
    assert diag["entry"]["data"][CONF_PASSWORD] == REDACTED
    assert diag["data"] is None


async def test_diagnostics_fallback_when_runtime_data_is_none(hass: HomeAssistant) -> None:
    """Test diagnostics falls back to hass.data when runtime_data is None."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="TP-Link PG2400P (Fallback)",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PASSWORD: "SecretPassword",
        },
        entry_id="mock_entry_fallback",
    )
    entry.add_to_hass(hass)

    mock_coordinator = MagicMock()
    mock_coordinator.data = _build_sample_data()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = mock_coordinator

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert "data" in diag
    assert diag["data"] is not None
    assert diag["data"]["device_info"]["model"] == "PG2400P"
