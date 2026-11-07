"""Binary sensor platform for TP-Link PG2400P integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .entity import PG2400PEntity
from .models import PG2400PData


@dataclass(frozen=True, kw_only=True)
class PG2400PBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Entity description for TP-Link PG2400P binary sensors."""

    value_fn: Callable[[PG2400PData], bool | None]
    attributes_fn: Callable[[PG2400PData], dict[str, Any]] | None = None


def _get_power_saving_mode(data: PG2400PData) -> bool:
    """Calculate whether adapter power saving mode is active."""
    dev = data.device_info
    lan_ps = getattr(dev, "power_saving_lan", getattr(dev, "lan_power_saving", False))
    msps_ps = getattr(dev, "power_saving_msps", getattr(dev, "traffic_power_saving", False))
    return bool(lan_ps or msps_ps)


def _get_power_saving_attributes(data: PG2400PData) -> dict[str, Any]:
    """Construct detailed attributes for power saving modes."""
    dev = data.device_info
    lan_ps = getattr(dev, "power_saving_lan", getattr(dev, "lan_power_saving", False))
    msps_ps = getattr(dev, "power_saving_msps", getattr(dev, "traffic_power_saving", False))
    return {
        "lan_power_saving": bool(lan_ps),
        "traffic_power_saving": bool(msps_ps),
    }


def _get_plc_link_attributes(data: PG2400PData) -> dict[str, Any]:
    """Construct detailed attributes for Powerline link state."""
    active_peers = [peer for peer in data.peers if peer.active]
    return {
        "active_peers_count": len(active_peers),
        "active_peer_macs": [peer.peer_mac for peer in active_peers],
        "domain_name": data.device_info.domain_name,
    }


BINARY_SENSOR_DESCRIPTIONS: tuple[PG2400PBinarySensorEntityDescription, ...] = (
    PG2400PBinarySensorEntityDescription(
        key="plc_link",
        translation_key="plc_link",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon="mdi:power-plug",
        value_fn=lambda data: data.plc_link_active,
        attributes_fn=_get_plc_link_attributes,
    ),
    PG2400PBinarySensorEntityDescription(
        key="power_saving",
        translation_key="power_saving",
        device_class=BinarySensorDeviceClass.POWER,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power-sleep",
        value_fn=_get_power_saving_mode,
        attributes_fn=_get_power_saving_attributes,
    ),
    PG2400PBinarySensorEntityDescription(
        key="xdsl_mitigation",
        translation_key="xdsl_mitigation",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:filter-outline",
        value_fn=lambda data: data.settings.xdsl_mitigation if data.settings else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PG2400PConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TP-Link PG2400P binary sensor platform from a config entry."""
    coordinator: PG2400PDataUpdateCoordinator = entry.runtime_data
    entities = [
        PG2400PBinarySensor(coordinator, description) for description in BINARY_SENSOR_DESCRIPTIONS
    ]
    async_add_entities(entities)


class PG2400PBinarySensor(PG2400PEntity, BinarySensorEntity):
    """Binary sensor entity for TP-Link PG2400P adapter."""

    entity_description: PG2400PBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: PG2400PBinarySensorEntityDescription,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator, description)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Return icon dynamically depending on state."""
        if self.entity_description.key == "plc_link":
            return "mdi:power-plug" if self.is_on else "mdi:power-plug-off"
        if self.entity_description.key == "power_saving":
            return "mdi:power-sleep" if self.is_on else "mdi:power-sleep-off"
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes of binary sensor."""
        if self.coordinator.data is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.coordinator.data)
