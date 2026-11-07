"""Switch entity platform for TP-Link PG2400P integration."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .entity import PG2400PEntity

_LOGGER = logging.getLogger(__name__)

LED_SWITCH_DESCRIPTION = SwitchEntityDescription(
    key="led",
    translation_key="led",
    device_class=SwitchDeviceClass.SWITCH,
    entity_category=EntityCategory.CONFIG,
    icon="mdi:led-on",
)

AUTO_COMPAT_SWITCH_DESCRIPTION = SwitchEntityDescription(
    key="automatic_compatibility",
    translation_key="automatic_compatibility",
    device_class=SwitchDeviceClass.SWITCH,
    entity_category=EntityCategory.CONFIG,
    icon="mdi:auto-fix",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PG2400PConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TP-Link PG2400P switches from a config entry."""
    coordinator: PG2400PDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        [
            PG2400PLedSwitch(coordinator, LED_SWITCH_DESCRIPTION),
            PG2400PAutoCompatSwitch(coordinator, AUTO_COMPAT_SWITCH_DESCRIPTION),
        ]
    )


class PG2400POptimisticSwitch(PG2400PEntity, SwitchEntity):
    """Base class for optimistic switches of TP-Link PG2400P adapter."""

    entity_description: SwitchEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: SwitchEntityDescription,
    ) -> None:
        """Initialize optimistic switch."""
        super().__init__(coordinator, description)
        self.entity_description = description
        self._optimistic_state: bool | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reset optimistic state on coordinator update confirmation."""
        self._optimistic_state = None
        super()._handle_coordinator_update()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_set_state(False)

    async def _async_execute_switch(self, enabled: bool) -> bool:
        """Execute switch command on device. Must be implemented by subclasses."""
        raise NotImplementedError

    async def _async_on_success(self, enabled: bool) -> None:
        """Hook called after successful command execution."""

    async def _async_set_state(self, enabled: bool) -> None:
        """Manage switch state with optimistic update and error recovery."""
        previous_state = self.is_on
        self._optimistic_state = enabled
        self.async_write_ha_state()

        try:
            success = await self._async_execute_switch(enabled)
            if not success:
                raise HomeAssistantError(
                    f"Adapter {self.coordinator.client.host} rejected command "
                    f"(отклонил команду): {self.entity_description.key}"
                )
            await self._async_on_success(enabled)
        except Exception as err:
            self._optimistic_state = previous_state
            self.async_write_ha_state()
            if isinstance(err, HomeAssistantError):
                raise
            _LOGGER.error(
                "Error toggling %s on %s: %s",
                self.entity_description.key,
                self.coordinator.client.host,
                err,
            )
            raise HomeAssistantError(
                f"Communication failure while toggling {self.entity_description.key} "
                f"on {self.coordinator.client.host} (Сбой связи): {err}"
            ) from err


class PG2400PLedSwitch(PG2400POptimisticSwitch):
    """Switch entity controlling front-panel LED indicators."""

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: SwitchEntityDescription = LED_SWITCH_DESCRIPTION,
    ) -> None:
        """Initialize LED switch entity."""
        super().__init__(coordinator, description)

    @property
    def is_on(self) -> bool | None:
        """Return current LED state accounting for optimistic state."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.led_enabled

    @property
    def icon(self) -> str:
        """Return dynamic icon based on LED state."""
        return "mdi:led-on" if self.is_on else "mdi:led-off"

    @property
    def available(self) -> bool:
        """Return True if adapter is online and LED status is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.led_enabled is not None
        )

    async def _async_execute_switch(self, enabled: bool) -> bool:
        """Execute LED switch command on device."""
        return await self.coordinator.client.set_led(enabled)

    async def _async_on_success(self, enabled: bool) -> None:
        """Update coordinator cache and request refresh."""
        if self.coordinator.data is not None:
            updated_data = dataclasses.replace(self.coordinator.data, led_enabled=enabled)
            self.coordinator.async_set_updated_data(updated_data)
        await self.coordinator.async_request_refresh()


class PG2400PAutoCompatSwitch(PG2400POptimisticSwitch):
    """Switch entity controlling automatic xDSL compatibility mitigation."""

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: SwitchEntityDescription = AUTO_COMPAT_SWITCH_DESCRIPTION,
    ) -> None:
        """Initialize automatic compatibility switch."""
        super().__init__(coordinator, description)

    @property
    def is_on(self) -> bool | None:
        """Return current automatic compatibility state."""
        if self._optimistic_state is not None:
            return self._optimistic_state
        if self.coordinator.data is None or self.coordinator.data.settings is None:
            return None
        return self.coordinator.data.settings.xdsl_mitigation

    @property
    def available(self) -> bool:
        """Return True if adapter is online and settings are available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.settings is not None
        )

    async def _async_execute_switch(self, enabled: bool) -> bool:
        """Execute automatic compatibility switch on device."""
        return await self.coordinator.client.set_automatic_compatibility(enabled)

    async def _async_on_success(self, enabled: bool) -> None:
        """Request refresh of coordinator settings."""
        await self.coordinator.async_refresh_settings()


# Backward compatibility aliases
PG2400PCompatibilitySwitch = PG2400PAutoCompatSwitch
PG2400PQosSwitch = PG2400POptimisticSwitch

__all__ = [
    "AUTO_COMPAT_SWITCH_DESCRIPTION",
    "LED_SWITCH_DESCRIPTION",
    "PG2400PAutoCompatSwitch",
    "PG2400PCompatibilitySwitch",
    "PG2400PLedSwitch",
    "PG2400POptimisticSwitch",
    "PG2400PQosSwitch",
    "async_setup_entry",
]
