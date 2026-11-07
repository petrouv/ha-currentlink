"""Diagnostics support for TP-Link PG2400P integration."""

from __future__ import annotations

import dataclasses
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import PG2400PDataUpdateCoordinator

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST,
    "password",
    "host",
    "ip_address",
    "mac_address",
    "peer_mac",
    "macs",
    "TOKEN",
    "token",
    "TCPIP.IPV4.IP_ADDRESS",
    "SYSTEM.PRODUCTION.MAC_ADDR",
    "DIDMNG.GENERAL.MACS",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Integration config entry.

    Returns:
        Dictionary containing redacted entry data and coordinator state.
    """
    coordinator: PG2400PDataUpdateCoordinator | None = getattr(entry, "runtime_data", None)
    if coordinator is None:
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    coordinator_data: dict[str, Any] | None = None
    if coordinator and coordinator.data:
        coordinator_data = dataclasses.asdict(coordinator.data)

    return {
        "entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "data": async_redact_data(coordinator_data, TO_REDACT) if coordinator_data else None,
    }
