"""Button platform for TP-Link PG2400P integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .entity import PG2400PEntity
from .errors import PG2400PError

_LOGGER = logging.getLogger(__name__)

REBOOT_BUTTON_DESCRIPTION = ButtonEntityDescription(
    key="reboot",
    translation_key="reboot",
    device_class=ButtonDeviceClass.RESTART,
    entity_category=EntityCategory.CONFIG,
    icon="mdi:restart",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PG2400PConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TP-Link PG2400P button platform from a config entry."""
    coordinator: PG2400PDataUpdateCoordinator = entry.runtime_data
    entities = [
        PG2400PRebootButton(coordinator, REBOOT_BUTTON_DESCRIPTION),
    ]
    async_add_entities(entities)


class PG2400PRebootButton(PG2400PEntity, ButtonEntity):
    """Reboot button entity for TP-Link PG2400P adapter."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: ButtonEntityDescription = REBOOT_BUTTON_DESCRIPTION,
    ) -> None:
        """Initialize reboot button."""
        super().__init__(coordinator, description)
        self.entity_description = description

    async def async_press(self) -> None:
        """Handle button press: send reboot command to adapter."""
        _LOGGER.info(
            "Initiated reboot for TP-Link PG2400P adapter (%s)",
            self.coordinator.client.host,
        )
        try:
            success = await self.coordinator.client.reboot()
            if not success:
                raise HomeAssistantError(
                    f"Adapter {self.coordinator.client.host} rejected command "
                    f"(отклонил команду): reboot"
                )
        except (PG2400PError, TimeoutError, OSError) as err:
            _LOGGER.error(
                "Error sending reboot command to %s: %s",
                self.coordinator.client.host,
                err,
            )
            raise HomeAssistantError(
                f"Failed to reboot adapter {self.coordinator.client.host}: {err}"
            ) from err
