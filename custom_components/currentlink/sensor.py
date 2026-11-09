"""Sensor entity platform for TP-Link PG2400P integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfLength,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .entity import PG2400PEntity, PG2400PPeerEntity
from .models import PeerLink, PG2400PData

PORT_SPEED_OPTIONS = [
    "Disconnected",
    "10M Half",
    "10M Full",
    "100M Half",
    "100M Full",
    "1000M Half",
    "1000M Full",
]

ETH_ERROR_KEYS: tuple[str, ...] = (
    "ETHA Tx errors",
    "ETHA Rx errors",
    "ETHB Tx errors",
    "ETHB Rx errors",
)

ETH_QUEUE_KEYS: tuple[str, ...] = (
    "ETHA Out Queue Full",
    "ETHB Out Queue Full",
)


@dataclass(frozen=True, kw_only=True)
class PG2400PSensorEntityDescription(SensorEntityDescription):
    """Entity description for TP-Link PG2400P adapter sensor."""

    value_fn: Callable[[PG2400PData], StateType | datetime]
    extra_state_attributes_fn: Callable[[PG2400PData], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class PG2400PPeerSensorEntityDescription(SensorEntityDescription):
    """Entity description for TP-Link PG2400P peer sensor."""

    value_fn: Callable[[PeerLink, PG2400PData], StateType | datetime]
    extra_state_attributes_fn: Callable[[PeerLink, PG2400PData], dict[str, Any]] | None = None


STATIC_SENSOR_DESCRIPTIONS: tuple[PG2400PSensorEntityDescription, ...] = (
    # 1. Ethernet physical ports
    PG2400PSensorEntityDescription(
        key="port1_speed",
        translation_key="port1_speed",
        device_class=SensorDeviceClass.ENUM,
        options=PORT_SPEED_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.port_status.port1_speed if data.port_status else "Disconnected",
        extra_state_attributes_fn=lambda data: {
            "port_code": data.port_status.port1_code if data.port_status else 0
        },
    ),
    PG2400PSensorEntityDescription(
        key="port2_speed",
        translation_key="port2_speed",
        device_class=SensorDeviceClass.ENUM,
        options=PORT_SPEED_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.port_status.port2_speed if data.port_status else "Disconnected",
        extra_state_attributes_fn=lambda data: {
            "port_code": data.port_status.port2_code if data.port_status else 0
        },
    ),
    # 2. Device uptime (UTC boot timestamp)
    PG2400PSensorEntityDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            dt_util.utcnow() - timedelta(seconds=data.telemetry.uptime_seconds)
            if data.telemetry and data.telemetry.uptime_seconds > 0
            else None
        ),
        extra_state_attributes_fn=lambda data: {
            "uptime_seconds": (round(data.telemetry.uptime_seconds, 1) if data.telemetry else None)
        },
    ),
    # 3. Physical line & PHY telemetry
    PG2400PSensorEntityDescription(
        key="attenuation",
        translation_key="attenuation",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.telemetry.attenuation_db if data.telemetry else None,
    ),
    PG2400PSensorEntityDescription(
        key="wire_length",
        translation_key="wire_length",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.telemetry.wire_length_m if data.telemetry else None,
    ),
    PG2400PSensorEntityDescription(
        key="throughput",
        translation_key="throughput",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.telemetry.xput_indicator_mbps if data.telemetry else None,
    ),
    # 4. Error diagnostics and interval metrics
    PG2400PSensorEntityDescription(
        key="retransmission_rate",
        translation_key="retransmission_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry_interval.retransmission_rate_pct if data.telemetry_interval else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="receive_bler",
        translation_key="receive_bler",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry_interval.receive_bler_pct if data.telemetry_interval else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="throughput_tx",
        translation_key="throughput_tx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry_interval.throughput_tx_mbps if data.telemetry_interval else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="throughput_rx",
        translation_key="throughput_rx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry_interval.throughput_rx_mbps if data.telemetry_interval else None
        ),
    ),
    # 5. Cumulative G.9962 counters
    PG2400PSensorEntityDescription(
        key="bytes_tx",
        translation_key="bytes_tx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("Bytes_TX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="bytes_rx",
        translation_key="bytes_rx",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("Bytes_RX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="pkts_tx",
        translation_key="pkts_tx",
        native_unit_of_measurement="pkts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("PKTS_TX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="pkts_rx",
        translation_key="pkts_rx",
        native_unit_of_measurement="pkts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("PKTS_RX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="blocks_tx",
        translation_key="blocks_tx",
        native_unit_of_measurement="blocks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("BLOCKS_TX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="blocks_rx",
        translation_key="blocks_rx",
        native_unit_of_measurement="blocks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("BLOCKS_RX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="blocks_rtx",
        translation_key="blocks_rtx",
        native_unit_of_measurement="blocks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("BLOCKS_RTX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="blocks_error_rx",
        translation_key="blocks_error_rx",
        native_unit_of_measurement="blocks",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.telemetry.g9962_counters.get("BLOCKS_ERROR_RX") if data.telemetry else None
        ),
    ),
    PG2400PSensorEntityDescription(
        key="channel_adaptations",
        translation_key="channel_adaptations",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            sum(data.telemetry.channel_adaptations.values())
            if data.telemetry and data.telemetry.channel_adaptations
            else 0
        ),
        extra_state_attributes_fn=lambda data: (
            data.telemetry.channel_adaptations if data.telemetry else {}
        ),
    ),
    PG2400PSensorEntityDescription(
        key="master_losses",
        translation_key="master_losses",
        native_unit_of_measurement="losses",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.telemetry.master_losses if data.telemetry else 0,
    ),
    PG2400PSensorEntityDescription(
        key="ethernet_errors",
        translation_key="ethernet_errors",
        native_unit_of_measurement="errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            sum(data.telemetry.eth_stats.get(k, 0) for k in ETH_ERROR_KEYS)
            if data.telemetry and data.telemetry.eth_stats
            else 0
        ),
        extra_state_attributes_fn=lambda data: (
            {
                k: data.telemetry.eth_stats[k]
                for k in (*ETH_ERROR_KEYS, *ETH_QUEUE_KEYS)
                if k in data.telemetry.eth_stats
            }
            if data.telemetry and data.telemetry.eth_stats
            else {}
        ),
    ),
    PG2400PSensorEntityDescription(
        key="qos_discarded_pkts",
        translation_key="qos_discarded_pkts",
        native_unit_of_measurement="pkts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.telemetry.qos_discarded_pkts if data.telemetry else 0,
    ),
    PG2400PSensorEntityDescription(
        key="llc_errors",
        translation_key="llc_errors",
        native_unit_of_measurement="errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            sum(data.telemetry.llc_errors.values())
            if data.telemetry and data.telemetry.llc_errors
            else 0
        ),
        extra_state_attributes_fn=lambda data: (
            data.telemetry.llc_errors if data.telemetry else {}
        ),
    ),
    # 6. Peer topology
    PG2400PSensorEntityDescription(
        key="active_peers",
        translation_key="active_peers",
        native_unit_of_measurement="peers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: len(data.peers) if data.peers else 0,
        extra_state_attributes_fn=lambda data: {
            "peer_macs": [p.peer_mac for p in data.peers] if data.peers else []
        },
    ),
    # 7. Radio configuration and profiles
    PG2400PSensorEntityDescription(
        key="phy_mode",
        translation_key="phy_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:antenna",
        value_fn=lambda data: data.settings.phy_mode if data.settings else None,
    ),
    PG2400PSensorEntityDescription(
        key="compatibility_mode",
        translation_key="compatibility_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transmission-tower",
        value_fn=lambda data: data.settings.compatibility_mode if data.settings else None,
    ),
    PG2400PSensorEntityDescription(
        key="qos_mode",
        translation_key="qos_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:speedometer",
        value_fn=lambda data: data.settings.qos_mode if data.settings else None,
    ),
)

PEER_SENSOR_DESCRIPTIONS: tuple[PG2400PPeerSensorEntityDescription, ...] = (
    PG2400PPeerSensorEntityDescription(
        key="tx_rate",
        translation_key="tx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda peer, data: peer.tx_rate_mbps,
        extra_state_attributes_fn=lambda peer, data: {
            "peer_mac": peer.peer_mac,
            "raw_bps": peer.tx_raw_bps,
        },
    ),
    PG2400PPeerSensorEntityDescription(
        key="rx_rate",
        translation_key="rx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda peer, data: peer.rx_rate_mbps,
        extra_state_attributes_fn=lambda peer, data: {
            "peer_mac": peer.peer_mac,
            "raw_bps": peer.rx_raw_bps,
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PG2400PConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TP-Link PG2400P sensor platform from a config entry."""
    coordinator: PG2400PDataUpdateCoordinator = entry.runtime_data

    # 1. Create static primary adapter sensors
    entities: list[SensorEntity] = [
        PG2400PSensor(coordinator, description) for description in STATIC_SENSOR_DESCRIPTIONS
    ]

    # 2. Create sensors for discovered peers
    known_peer_macs: set[str] = set()
    if coordinator.data and coordinator.data.peers:
        for peer in coordinator.data.peers:
            known_peer_macs.add(peer.peer_mac.lower())
            for desc in PEER_SENSOR_DESCRIPTIONS:
                entities.append(PG2400PPeerSensor(coordinator, peer.peer_mac, desc))

    async_add_entities(entities)

    # 3. Dynamic peer discovery listener
    @callback
    def _async_check_new_peers() -> None:
        if not coordinator.data or not coordinator.data.peers:
            return
        new_peer_entities: list[SensorEntity] = []
        for peer in coordinator.data.peers:
            mac_lower = peer.peer_mac.lower()
            if mac_lower not in known_peer_macs:
                known_peer_macs.add(mac_lower)
                for desc in PEER_SENSOR_DESCRIPTIONS:
                    new_peer_entities.append(PG2400PPeerSensor(coordinator, peer.peer_mac, desc))
        if new_peer_entities:
            async_add_entities(new_peer_entities)

    entry.async_on_unload(coordinator.async_add_listener(_async_check_new_peers))


class PG2400PSensor(PG2400PEntity, SensorEntity):
    """Sensor entity for TP-Link PG2400P adapter."""

    entity_description: PG2400PSensorEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: PG2400PSensorEntityDescription,
    ) -> None:
        """Initialize adapter sensor."""
        super().__init__(coordinator, description)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return current sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes."""
        if (
            self.entity_description.extra_state_attributes_fn is not None
            and self.coordinator.data is not None
        ):
            return self.entity_description.extra_state_attributes_fn(self.coordinator.data)
        return None


class PG2400PPeerSensor(PG2400PPeerEntity, SensorEntity):
    """Sensor entity for remote TP-Link PG2400P peer."""

    entity_description: PG2400PPeerSensorEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        peer_mac: str,
        description: PG2400PPeerSensorEntityDescription,
        *,
        as_child_device: bool = False,
    ) -> None:
        """Initialize peer sensor."""
        super().__init__(coordinator, peer_mac, description, as_child_device=as_child_device)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return current rate or metric for this peer."""
        if self.coordinator.data is None:
            return None
        peer = self._peer_link
        if peer is None:
            return None
        return self.entity_description.value_fn(peer, self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes for peer sensor."""
        attrs: dict[str, Any] = {"peer_mac": self.peer_mac}
        if self.coordinator.data is not None:
            peer = self._peer_link
            if peer is not None and self.entity_description.extra_state_attributes_fn is not None:
                attrs.update(
                    self.entity_description.extra_state_attributes_fn(peer, self.coordinator.data)
                )
        return attrs
