"""TP-Link PG2400P Powerline integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .client import PG2400PClient
from .const import (
    DEFAULT_TIMEOUT,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import PG2400PConfigEntry, PG2400PDataUpdateCoordinator
from .errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    PG2400PError,
    ResponseFormatError,
    TokenExpiredError,
)
from .models import (
    DeviceInfo,
    PeerLink,
    PG2400PData,
    PortSpeedCode,
    PortStatus,
    PowerlineSettings,
    TelemetryInterval,
    TelemetrySnapshot,
    compute_counter_delta,
    compute_telemetry_interval,
    decode_telemetry_snapshot,
)
from .protocol import (
    ERROR_INVALID_PASSWORD,
    ERROR_SESSION_WARMUP,
    ERROR_SUCCESS,
    ERROR_TOKEN_INVALID,
    ERROR_UNSUPPORTED,
    compute_password_hash,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

__all__ = [
    "CONFIG_SCHEMA",
    "DOMAIN",
    "PLATFORMS",
    "ERROR_INVALID_PASSWORD",
    "ERROR_SESSION_WARMUP",
    "ERROR_SUCCESS",
    "ERROR_TOKEN_INVALID",
    "ERROR_UNSUPPORTED",
    "AuthenticationError",
    "ConnectionTimeoutError",
    "DeviceInfo",
    "PG2400PClient",
    "PG2400PConfigEntry",
    "PG2400PConnectionError",
    "PG2400PData",
    "PG2400PDataUpdateCoordinator",
    "PG2400PError",
    "PeerLink",
    "PortSpeedCode",
    "PortStatus",
    "PowerlineSettings",
    "ResponseFormatError",
    "TelemetryInterval",
    "TelemetrySnapshot",
    "TokenExpiredError",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
    "async_update_options",
    "compute_counter_delta",
    "compute_password_hash",
    "compute_telemetry_interval",
    "decode_telemetry_snapshot",
    "parse_response",
]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the TP-Link PG2400P component from YAML (not used, returns True)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PG2400PConfigEntry) -> bool:
    """Set up TP-Link PG2400P integration from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Integration config entry.

    Returns:
        True if setup succeeded.
    """
    host: str = entry.data[CONF_HOST]
    password: str = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    client = PG2400PClient(
        host=host,
        password=password,
        session=session,
        timeout=DEFAULT_TIMEOUT,
    )

    coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)

    # Initial coordinator poll: raises ConfigEntryAuthFailed on auth error or
    # ConfigEntryNotReady on connection failure for automatic retry.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options flow update listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PG2400PConfigEntry) -> bool:
    """Unload a TP-Link PG2400P config entry.

    Args:
        hass: Home Assistant instance.
        entry: Integration config entry.

    Returns:
        True if unload succeeded.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: PG2400PDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).pop(
            entry.entry_id, None
        )
        if coordinator is None and hasattr(entry, "runtime_data"):
            coordinator = entry.runtime_data

        if coordinator and coordinator.client:
            await coordinator.client.close()

        if DOMAIN in hass.data and not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok
