"""Base entity classes for TP-Link PG2400P integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_NAME, DOMAIN, MANUFACTURER, MODEL
from .coordinator import PG2400PDataUpdateCoordinator
from .models import PeerLink


class PG2400PEntity(CoordinatorEntity[PG2400PDataUpdateCoordinator]):
    """Base entity class for primary TP-Link PG2400P adapter entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: EntityDescription | None = None,
    ) -> None:
        """Initialize base adapter entity.

        Args:
            coordinator: Data update coordinator instance.
            description: Entity description (optional).
        """
        super().__init__(coordinator)
        if description is not None:
            self.entity_description = description
            dev = coordinator.data.device_info
            self._attr_unique_id = f"{dev.mac_address.lower()}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for Home Assistant Device Registry."""
        dev = self.coordinator.data.device_info
        entry = getattr(self.coordinator, "config_entry", None) or getattr(
            self.coordinator, "entry", None
        )
        host = entry.data.get(CONF_HOST, dev.ip_address) if entry else dev.ip_address
        mac_lower = dev.mac_address.lower()
        return DeviceInfo(
            identifiers={(DOMAIN, mac_lower)},
            connections={(CONNECTION_NETWORK_MAC, mac_lower)},
            name=dev.device_name or DEFAULT_NAME,
            manufacturer=MANUFACTURER,
            model=MODEL,
            sw_version=dev.fw_version,
            hw_version=dev.hw_revision,
            configuration_url=f"http://{host}",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available based on coordinator state."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and self.coordinator.data.device_info is not None
        )


class PG2400PPeerEntity(PG2400PEntity):
    """Base entity class for entities representing remote Powerline peer links."""

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        peer_mac: str,
        description: EntityDescription,
        *,
        as_child_device: bool = False,
    ) -> None:
        """Initialize peer entity.

        Args:
            coordinator: Data update coordinator instance.
            peer_mac: MAC address of remote adapter.
            description: Entity description.
            as_child_device: Whether to register as child device via via_device.
        """
        super().__init__(coordinator)
        self.peer_mac = peer_mac.lower()
        self.entity_description = description
        self._as_child_device = as_child_device

        local_mac = coordinator.data.device_info.mac_address.lower()
        self._attr_unique_id = f"{local_mac}_{self.peer_mac}_{description.key}"
        self._attr_translation_placeholders = {"peer_mac": self.peer_mac}

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info: local adapter or child peer device."""
        if not self._as_child_device:
            return super().device_info

        local_mac = self.coordinator.data.device_info.mac_address.lower()
        return DeviceInfo(
            identifiers={(DOMAIN, self.peer_mac)},
            connections={(CONNECTION_NETWORK_MAC, self.peer_mac)},
            name=f"PG2400P Peer ({self.peer_mac})",
            manufacturer=MANUFACTURER,
            model=MODEL,
            via_device=(DOMAIN, local_mac),
        )

    @property
    def _peer_link(self) -> PeerLink | None:
        """Find current PeerLink object in coordinator data."""
        if not self.coordinator.data or not self.coordinator.data.peers:
            return None
        for p in self.coordinator.data.peers:
            if p.peer_mac.lower() == self.peer_mac:
                return p
        return None

    @property
    def available(self) -> bool:
        """Return True if coordinator is active and peer is present and active."""
        if not super().available:
            return False
        peer = self._peer_link
        return peer is not None and peer.active
