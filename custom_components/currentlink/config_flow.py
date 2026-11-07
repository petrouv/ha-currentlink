"""Config flow for TP-Link PG2400P integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import PG2400PClient
from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
)
from .models import DeviceInfo

_LOGGER = logging.getLogger(__name__)

# User input schema for initial setup
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)


async def _validate_input(
    hass: HomeAssistant, host: str, password: str
) -> tuple[dict[str, str], DeviceInfo | None]:
    """Validate connection to adapter and retrieve device info.

    Args:
        hass: Home Assistant instance.
        host: Hostname or IP address of the adapter.
        password: Web interface password.

    Returns:
        Tuple of (errors_dict, device_info).
    """
    errors: dict[str, str] = {}
    device_info: DeviceInfo | None = None
    session = async_get_clientsession(hass)
    client: PG2400PClient | None = None

    try:
        client = PG2400PClient(host, password, session=session)
        await client.login()
        device_info = await client.get_device_info()
    except AuthenticationError:
        errors["base"] = "invalid_auth"
    except (ValueError, PG2400PConnectionError, ConnectionTimeoutError, TimeoutError):
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error verifying adapter %s", host)
        errors["base"] = "unknown"
    finally:
        if client is not None:
            await client.close()

    return errors, device_info


class PG2400PConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TP-Link PG2400P."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle user input when adding the integration.

        Args:
            user_input: Form data (host and password) or None.

        Returns:
            Config flow result (form, entry creation, or error).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_host = user_input[CONF_HOST].strip()
            lower_host = raw_host.lower()
            if lower_host.startswith("http://"):
                raw_host = raw_host[len("http://") :]
            elif lower_host.startswith("https://"):
                raw_host = raw_host[len("https://") :]
            host = raw_host.rstrip("/").strip()
            password = user_input[CONF_PASSWORD]

            errors, info = await _validate_input(self.hass, host, password)
            if not errors and info is not None:
                # Set unique ID from hardware MAC address
                await self.async_set_unique_id(info.mac_address)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})

                title = f"TP-Link PG2400P ({info.device_name or info.mac_address})"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle re-authentication flow when credentials change or expire.

        Args:
            entry_data: Existing saved entry data.

        Returns:
            Proceeds to re-auth confirmation step.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm and enter updated password for existing entry.

        Args:
            user_input: Form data with new password or None.

        Returns:
            Update credentials and finish flow, or redisplay form with error.
        """
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            host = reauth_entry.data[CONF_HOST]
            password = user_input[CONF_PASSWORD]

            errors, _info = await _validate_input(self.hass, host, password)
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: password},
                )

        reauth_schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=reauth_schema,
            errors=errors,
            description_placeholders={
                "host": reauth_entry.data.get(CONF_HOST, ""),
                "title": reauth_entry.title,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get options flow handler for this config entry.

        Args:
            config_entry: Config entry to configure.

        Returns:
            Options flow handler instance.
        """
        return PG2400POptionsFlow(config_entry)


class PG2400POptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for TP-Link PG2400P."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow handler."""
        if config_entry is not None:
            self._config_entry = config_entry

    @property
    def config_entry(self) -> config_entries.ConfigEntry:
        """Return associated config entry."""
        if hasattr(self, "_config_entry"):
            return self._config_entry
        return super().config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage configurable options of integration (polling interval).

        Args:
            user_input: Form data containing scan interval or None.

        Returns:
            Step result (form display or options saved).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                raw_interval = user_input.get(CONF_SCAN_INTERVAL)
                if raw_interval is None or isinstance(raw_interval, bool):
                    errors["base"] = "invalid_scan_interval"
                else:
                    scan_interval = int(raw_interval)
                    if float(raw_interval) != float(scan_interval):
                        errors["base"] = "invalid_scan_interval"
                    elif MIN_SCAN_INTERVAL <= scan_interval <= MAX_SCAN_INTERVAL:
                        return self.async_create_entry(
                            title="",
                            data={
                                CONF_SCAN_INTERVAL: scan_interval,
                            },
                        )
                    else:
                        errors["base"] = "invalid_scan_interval"
            except (ValueError, TypeError):
                errors["base"] = "invalid_scan_interval"

        schema = self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=DEFAULT_SCAN_INTERVAL,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                            unit_of_measurement="s",
                        )
                    ),
                }
            ),
            self.config_entry.options,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
