"""DataUpdateCoordinator for TP-Link PG2400P integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import LED_ENABLE_KEY, PG2400PClient
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .errors import AuthenticationError, PG2400PError
from .models import (
    PG2400PData,
    PortStatus,
    PowerlineSettings,
    TelemetryInterval,
    TelemetrySnapshot,
    compute_telemetry_interval,
)

_LOGGER = logging.getLogger(__name__)

type PG2400PConfigEntry = ConfigEntry[PG2400PDataUpdateCoordinator]


class PG2400PDataUpdateCoordinator(DataUpdateCoordinator[PG2400PData]):
    """Data coordinator for polling TP-Link PG2400P adapter state."""

    config_entry: PG2400PConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PG2400PConfigEntry,
        client: PG2400PClient,
    ) -> None:
        """Initialize coordinator.

        Args:
            hass: Home Assistant instance.
            entry: Integration config entry.
            client: PG2400PClient instance.
        """
        self.entry = entry
        self.client = client
        self._prev_telemetry: TelemetrySnapshot | None = None
        self._powerline_settings: PowerlineSettings | None = None

        scan_interval_sec: int = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        update_interval = timedelta(seconds=scan_interval_sec)

        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} ({client.host})",
            update_interval=update_interval,
        )

    async def _async_update_data(self) -> PG2400PData:
        """Poll adapter and construct aggregated state."""
        try:
            device_info = await self.client.get_device_info()
            peers = await self.client.get_peers()
            telemetry = await self.client.get_telemetry()
            port_status = await self._async_fetch_port_status()
            led_enabled = await self._async_fetch_led_state()

            if self._powerline_settings is None:
                try:
                    self._powerline_settings = await self.client.get_powerline_settings()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug(
                        "Failed to read radio settings for %s: %s",
                        self.client.host,
                        exc,
                    )

        except AuthenticationError as err:
            _LOGGER.error(
                "Authentication error on TP-Link PG2400P (%s): %s",
                self.client.host,
                err,
            )
            raise ConfigEntryAuthFailed(
                f"Invalid password for adapter {self.client.host} "
                f"(Неверный пароль для адаптера): {err}"
            ) from err

        except (PG2400PError, aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "Communication failure with TP-Link PG2400P (%s): %s",
                self.client.host,
                err,
            )
            raise UpdateFailed(
                f"Communication failure with adapter {self.client.host}: {err}"
            ) from err

        # Compute G.9962 interval metrics (BLER, Retransmissions, Throughput)
        telemetry_interval: TelemetryInterval | None = None
        if self._prev_telemetry is not None:
            try:
                telemetry_interval = compute_telemetry_interval(
                    prev=self._prev_telemetry,
                    curr=telemetry,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug(
                    "Failed to compute interval telemetry for %s: %s",
                    self.client.host,
                    err,
                )
                telemetry_interval = None

        self._prev_telemetry = telemetry

        return PG2400PData(
            device_info=device_info,
            peers=peers,
            port_status=port_status,
            telemetry=telemetry,
            telemetry_interval=telemetry_interval,
            led_enabled=led_enabled,
            settings=self._powerline_settings,
        )

    async def async_refresh_settings(self) -> None:
        """Force refresh of radio parameters and adapter profiles."""
        try:
            self._powerline_settings = await self.client.get_powerline_settings()
            if self.data is not None:
                self.async_set_updated_data(
                    PG2400PData(
                        device_info=self.data.device_info,
                        peers=self.data.peers,
                        port_status=self.data.port_status,
                        telemetry=self.data.telemetry,
                        telemetry_interval=self.data.telemetry_interval,
                        led_enabled=self.data.led_enabled,
                        settings=self._powerline_settings,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to refresh radio settings for %s: %s",
                self.client.host,
                exc,
            )

    async def _async_fetch_port_status(self) -> PortStatus | None:
        """Fetch status of physical Ethernet ports on adapter."""
        try:
            if hasattr(self.client, "get_port_status"):
                res = await self.client.get_port_status()
                if isinstance(res, PortStatus):
                    return res
            # Fallback for unconfigured mocks in test suite or raw post
            raw = await self.client.post({"COMMAND": "lan link speed"})
            p1 = int(raw.get("PORT1", 0))
            p2 = int(raw.get("PORT2", 0))
            return PortStatus.from_codes(p1, p2)
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to read port status for %s: %s",
                self.client.host,
                exc,
            )
            return None

    async def _async_fetch_led_state(self) -> bool | None:
        """Fetch adapter LED status."""
        try:
            if hasattr(self.client, "get_led_state"):
                res = await self.client.get_led_state()
                if isinstance(res, bool):
                    return res
            # Fallback for unconfigured mocks in test suite or raw query
            raw = await self.client.query([LED_ENABLE_KEY])
            val = raw.get(LED_ENABLE_KEY, "").strip().upper()
            if val in ("1", "ON", "TRUE", "YES"):
                return True
            if val in ("0", "OFF", "FALSE", "NO"):
                return False
            return None
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "Failed to read LED state for %s: %s",
                self.client.host,
                exc,
            )
            return None
