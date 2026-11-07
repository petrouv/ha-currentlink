"""Select entity platform for TP-Link PG2400P mode configuration."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .entity import PG2400PEntity
from .models import PG2400PData

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PG2400PSelectEntityDescription(SelectEntityDescription):
    """Entity description for TP-Link PG2400P select entity."""

    current_option_fn: Callable[[PG2400PData], str | None]
    set_option_fn: Callable[[PG2400PDataUpdateCoordinator, str], Coroutine[Any, Any, bool]]


SELECT_DESCRIPTIONS: tuple[PG2400PSelectEntityDescription, ...] = (
    PG2400PSelectEntityDescription(
        key="phy_mode",
        translation_key="phy_mode",
        options=["MIMO", "SISO"],
        entity_category=EntityCategory.CONFIG,
        icon="mdi:antenna",
        current_option_fn=lambda data: data.settings.phy_mode if data.settings else None,
        set_option_fn=lambda coord, opt: coord.client.set_phy_mode(opt),
    ),
    PG2400PSelectEntityDescription(
        key="vdsl_compatibility_mode",
        translation_key="vdsl_compatibility_mode",
        options=["Full Power", "VDSL 17a", "VDSL 35b"],
        entity_category=EntityCategory.CONFIG,
        icon="mdi:transmission-tower",
        current_option_fn=lambda data: data.settings.compatibility_mode if data.settings else None,
        set_option_fn=lambda coord, opt: coord.client.set_compatibility_mode(opt),
    ),
    PG2400PSelectEntityDescription(
        key="qos_mode",
        translation_key="qos_mode",
        options=["Fair", "Gaming", "Streaming", "Voice"],
        entity_category=EntityCategory.CONFIG,
        icon="mdi:speedometer",
        current_option_fn=lambda data: data.settings.qos_mode if data.settings else None,
        set_option_fn=lambda coord, opt: coord.client.set_qos_mode(opt),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PG2400PConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TP-Link PG2400P select entities from a config entry."""
    coordinator: PG2400PDataUpdateCoordinator = entry.runtime_data
    entities = [PG2400PSelect(coordinator, desc) for desc in SELECT_DESCRIPTIONS]
    async_add_entities(entities)


class PG2400PSelect(PG2400PEntity, SelectEntity):
    """Select entity for TP-Link PG2400P adapter mode configuration."""

    entity_description: PG2400PSelectEntityDescription

    def __init__(
        self,
        coordinator: PG2400PDataUpdateCoordinator,
        description: PG2400PSelectEntityDescription,
    ) -> None:
        """Initialize mode select entity."""
        super().__init__(coordinator, description)
        self.entity_description = description
        self._current_option: str | None = None

    @property
    def current_option(self) -> str | None:
        """Return currently selected option."""
        if self._current_option is not None:
            return self._current_option
        if self.coordinator.data is None:
            return None
        return self.entity_description.current_option_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        """Return True if adapter is online and settings are available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.settings is not None
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Reset optimistic option on coordinator update."""
        self._current_option = None
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change selected device mode."""
        if option not in self.entity_description.options:
            raise HomeAssistantError(f"Invalid option: {option}")

        previous_option = self.current_option
        self._current_option = option
        self.async_write_ha_state()

        try:
            success = await self.entity_description.set_option_fn(self.coordinator, option)
            if not success:
                raise HomeAssistantError(
                    f"Adapter {self.coordinator.client.host} rejected option {option}"
                )
            await self.coordinator.async_refresh_settings()
        except Exception as err:
            self._current_option = previous_option
            self.async_write_ha_state()
            if isinstance(err, HomeAssistantError):
                raise
            _LOGGER.error(
                "Error setting option %s on %s: %s",
                option,
                self.coordinator.client.host,
                err,
            )
            raise HomeAssistantError(
                f"Failed to set option {option} on {self.coordinator.client.host}: {err}"
            ) from err
