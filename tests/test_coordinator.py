"""Тесты для DataUpdateCoordinator и жизненного цикла TP-Link PG2400P."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink import (
    async_setup,
    async_setup_entry,
    async_unload_entry,
    async_update_options,
)
from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    PLATFORMS,
)
from custom_components.currentlink.coordinator import PG2400PDataUpdateCoordinator
from custom_components.currentlink.errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    ResponseFormatError,
)
from custom_components.currentlink.models import (
    DeviceInfo,
    PeerLink,
    TelemetrySnapshot,
)

TEST_HOST = "192.0.2.10"
TEST_PASSWORD = "secret_password"
TEST_MAC = "aa:bb:cc:00:11:22"
PEER_MAC = "aa:bb:cc:00:11:33"

MOCK_DEVICE_INFO = DeviceInfo(
    model="PG2400P",
    hw_revision="1.0",
    fw_version="1.0.3 Build 20221213 Rel.62540",
    mac_address=TEST_MAC,
    device_name="plc-main",
    domain_name="HomeNetwork",
    ip_address=TEST_HOST,
)

MOCK_PEER = PeerLink(
    peer_mac=PEER_MAC,
    rx_rate_mbps=1254.5,
    tx_rate_mbps=1180.25,
    rx_raw=40144,
    tx_raw=37768,
    active=True,
)


def _build_mock_client(
    device_info: DeviceInfo = MOCK_DEVICE_INFO,
    peers: list[PeerLink] | None = None,
    telemetry: TelemetrySnapshot | None = None,
    port1: int = 6,
    port2: int = 0,
    led_enabled: bool = True,
) -> AsyncMock:
    """Создает мок клиента PG2400PClient со стандартными ответами."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = device_info
    client.get_peers.return_value = [MOCK_PEER] if peers is None else peers
    if telemetry is not None:
        client.get_telemetry.return_value = telemetry
    client.post.return_value = {"PORT1": str(port1), "PORT2": str(port2)}
    client.query.return_value = {LED_ENABLE_KEY: "1" if led_enabled else "0"}
    client.close.return_value = None
    return client


# =========================================================================
# 1. Тесты опроса данных координатора (TestCoordinatorPolling)
# =========================================================================


class TestCoordinatorPolling:
    """Проверка первичного и регулярного опроса адаптера."""

    async def test_coordinator_first_refresh_success(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет успешный первый сбор данных и заполнение модели PG2400PData."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        assert coordinator.last_update_success is True
        assert coordinator.data is not None
        assert coordinator.data.device_info.mac_address == TEST_MAC
        assert coordinator.data.device_info.model == "PG2400P"
        assert len(coordinator.data.peers) == 1
        assert coordinator.data.peers[0].peer_mac == PEER_MAC
        assert coordinator.data.plc_link_active is True
        assert coordinator.data.port_status is not None
        assert coordinator.data.port_status.port1_speed == "1000M Full"
        assert coordinator.data.port_status.port2_speed == "Disconnected"
        assert coordinator.data.led_enabled is True
        # При первом опросе нет базовой линии, поэтому telemetry_interval равен None
        assert coordinator.data.telemetry_interval is None


# =========================================================================
# 2. Тесты сохранения и расчёта дельт (TestCoordinatorDeltaPreservation)
# =========================================================================


class TestCoordinatorDeltaPreservation:
    """Проверка расчёта интервальных метрик и сброса при перезагрузке."""

    async def test_delta_preservation_across_consecutive_polls(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет расчёт дельт G.9962 (BLER, RTX, throughput) между двумя опросами."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()
        assert coordinator.data.telemetry_interval is None

        # Второй опрос: снимок спустя 89.96 с с изменившимися счётчиками
        client.get_telemetry.return_value = sample_snapshot_2
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        interval = coordinator.data.telemetry_interval
        assert interval is not None
        assert interval.duration_seconds == 89.96
        assert interval.tx_bytes == 247680
        assert interval.rx_bytes == 108000
        assert interval.blocks_tx == 1697
        assert interval.blocks_rx == 698
        assert interval.blocks_rtx == 1
        assert interval.blocks_error_rx == 19
        assert round(interval.receive_bler_pct, 4) == 2.7221
        assert round(interval.retransmission_rate_pct, 4) == 0.0589

    async def test_reboot_detection_resets_telemetry_interval(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет сброс базовой линии при уменьшении аптайма (перезагрузка устройства)."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        client.get_telemetry.return_value = sample_snapshot_2
        await coordinator.async_refresh()
        assert coordinator.data.telemetry_interval is not None

        # Третий снимок: устройство перезагрузилось, аптайм упал до 10 секунд
        reboot_snapshot = dataclasses.replace(sample_snapshot_2, uptime_seconds=10.0)
        client.get_telemetry.return_value = reboot_snapshot
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        # После обнаружения перезагрузки интервал сбрасывается в None без падения координатора
        assert coordinator.data.telemetry_interval is None


# =========================================================================
# 3. Тесты обработки ошибок (TestCoordinatorErrorHandling)
# =========================================================================


class TestCoordinatorErrorHandling:
    """Проверка маппинга ошибок и устойчивости к сбоям сети/авторизации."""

    async def test_auth_error_raises_config_entry_auth_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет, что AuthenticationError преобразуется в ConfigEntryAuthFailed."""
        client = _build_mock_client()
        client.get_device_info.side_effect = AuthenticationError(
            "Invalid credentials", login_attempts=1
        )
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_config_entry_first_refresh()

    async def test_network_error_raises_config_entry_not_ready_on_first_refresh(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет, что при сбое связи при первом запуске возбуждается ConfigEntryNotReady."""
        client = _build_mock_client()
        client.get_device_info.side_effect = PG2400PConnectionError("Connection refused")
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        with pytest.raises(ConfigEntryNotReady):
            await coordinator.async_config_entry_first_refresh()

    async def test_network_error_sets_update_failed_on_subsequent_refresh(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что сетевой сбой при регулярном опросе вызывает UpdateFailed."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()
        assert coordinator.last_update_success is True

        client.get_device_info.side_effect = PG2400PConnectionError("Network down")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

    async def test_timeout_sets_update_failed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обработку таймаута при запросе телеметрии."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        client.get_telemetry.side_effect = ConnectionTimeoutError("Request timed out")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

    async def test_auxiliary_metrics_graceful_degradation(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет мягкую деградацию при ошибках чтения статуса портов и светодиодов."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.post.side_effect = ResponseFormatError("Port command unsupported")
        client.query.side_effect = ResponseFormatError("LED key not supported")

        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        # Основные данные получены, вспомогательные поля аккуратно сброшены в None
        assert coordinator.last_update_success is True
        assert coordinator.data.port_status is None
        assert coordinator.data.led_enabled is None
        assert coordinator.data.device_info.mac_address == TEST_MAC
        assert coordinator.data.telemetry is not None

    async def test_auxiliary_metrics_auth_error_propagates(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что ошибка аутентификации во вспомогательных запросах не подавляется."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.post.side_effect = AuthenticationError("Token expired mid-poll")

        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_config_entry_first_refresh()


# =========================================================================
# 4. Тесты жизненного цикла интеграции (__init__.py)
# =========================================================================


class TestLifecycleInitUnload:
    """Проверка async_setup, async_setup_entry, async_unload_entry и options listener."""

    async def test_async_setup_returns_true(self, hass: HomeAssistant) -> None:
        """Проверяет инициализацию интеграции через async_setup."""
        assert await async_setup(hass, {}) is True
        assert DOMAIN in hass.data

    async def test_async_setup_entry_populates_data_and_runtime_data(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет инициализацию ConfigEntry, создание координатора и форвардинг платформ."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        mock_client = _build_mock_client(telemetry=sample_snapshot_1)

        with (
            patch(
                "custom_components.currentlink.PG2400PClient",
                return_value=mock_client,
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                return_value=True,
            ) as mock_forward,
        ):
            success = await async_setup_entry(hass, entry)

        assert success is True
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert isinstance(coordinator, PG2400PDataUpdateCoordinator)
        assert entry.runtime_data is coordinator
        mock_forward.assert_awaited_once_with(entry, PLATFORMS)

    async def test_async_unload_entry_cleans_up_client_and_data(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет выгрузку ConfigEntry, закрытие сессии клиента и очистку hass.data."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        mock_client = _build_mock_client(telemetry=sample_snapshot_1)

        with (
            patch(
                "custom_components.currentlink.PG2400PClient",
                return_value=mock_client,
            ),
            patch.object(
                hass.config_entries,
                "async_forward_entry_setups",
                return_value=True,
            ),
        ):
            await async_setup_entry(hass, entry)

        assert entry.entry_id in hass.data[DOMAIN]

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            return_value=True,
        ) as mock_unload:
            unload_ok = await async_unload_entry(hass, entry)

        assert unload_ok is True
        mock_unload.assert_awaited_once_with(entry, PLATFORMS)
        mock_client.close.assert_awaited_once()
        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    async def test_async_update_options_reloads_entry(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет вызов async_reload при обновлении опций."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)

        with patch.object(
            hass.config_entries,
            "async_reload",
            return_value=True,
        ) as mock_reload:
            await async_update_options(hass, entry)

        mock_reload.assert_awaited_once_with(entry.entry_id)
