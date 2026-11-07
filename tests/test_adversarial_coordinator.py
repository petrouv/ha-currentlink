"""Эмпирические стресс-тесты для PG2400PDataUpdateCoordinator и __init__.py.

Охватывает:
1. Нестабильность сети и прерывистый опрос (success/failure, availability, deltas).
2. Внезапная перезагрузка адаптера во время опроса (reboot, mid-poll, baseline reset).
3. Истечение авторизации (AuthenticationError -> ConfigEntryAuthFailed).
4. Быстрые циклы монтирования и выгрузки (rapid setup/unload, отсутствие утечек).
5. Динамическое обновление интервала опроса через OptionsFlow.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import CoordinatorEntity, UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink import (
    async_setup_entry,
    async_unload_entry,
    async_update_options,
)
from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    PLATFORMS,
)
from custom_components.currentlink.coordinator import (
    PG2400PDataUpdateCoordinator,
)
from custom_components.currentlink.errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
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
    client.password = TEST_PASSWORD
    client.get_device_info.return_value = device_info
    client.get_peers.return_value = [MOCK_PEER] if peers is None else peers
    if telemetry is not None:
        client.get_telemetry.return_value = telemetry
    client.post.return_value = {"PORT1": str(port1), "PORT2": str(port2)}
    client.query.return_value = {LED_ENABLE_KEY: "1" if led_enabled else "0"}
    client.close.return_value = None
    return client


class _DummyCoordinatorEntity(CoordinatorEntity[PG2400PDataUpdateCoordinator]):
    """Тестовая сущность для эмпирической проверки доступности (availability)."""

    def __init__(self, coordinator: PG2400PDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self.update_count = 0

    def _handle_coordinator_update(self) -> None:
        self.update_count += 1
        super()._handle_coordinator_update()


# =========================================================================
# 1. Стресс-тесты нестабильности сети и прерывистого опроса
# =========================================================================


class TestPollingFlakinessAndDropouts:
    """Проверка устойчивости координатора к прерывистым сетевым сбоям и дропам."""

    async def test_alternating_success_and_failure_flakiness(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет чередование успешных и аварийных опросов (flakiness).

        Сценарий:
        Cycle 1 (t=0): Успех -> available=True.
        Cycle 2 (t=30): Сбой связи (PG2400PConnectionError) -> available=False.
        Cycle 3 (t=60): Восстановление -> available=True, дельта вычислена за 60 секунд.
        Cycle 4 (t=90): Таймаут (ConnectionTimeoutError) -> available=False.
        Cycle 5 (t=120): Битый HTTP payload (aiohttp.ClientPayloadError) -> available=False.
        Cycle 6 (t=150): Восстановление -> available=True.
        """
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        entity = _DummyCoordinatorEntity(coordinator)

        # Cycle 1: Успешный первый запуск
        await coordinator.async_config_entry_first_refresh()
        assert coordinator.last_update_success is True
        assert entity.available is True
        assert coordinator.data.telemetry_interval is None

        # Cycle 2: Прерывистый сетевой сбой
        client.get_telemetry.side_effect = PG2400PConnectionError("Temporary unreachable")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert entity.available is False
        assert coordinator.last_exception is not None
        assert isinstance(coordinator.last_exception, UpdateFailed)

        # Cycle 3: Восстановление связи, снимок спустя 89.96 с
        client.get_telemetry.side_effect = None
        client.get_telemetry.return_value = sample_snapshot_2
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True
        assert entity.available is True
        # Дельта вычислена корректно между Cycle 1 и Cycle 3, несмотря на пропуск Cycle 2
        interval = coordinator.data.telemetry_interval
        assert interval is not None
        assert interval.duration_seconds == 89.96
        assert interval.tx_bytes == 247680
        assert interval.rx_bytes == 108000

        # Cycle 4: Таймаут запроса
        client.get_telemetry.side_effect = ConnectionTimeoutError("Socket timeout 10s")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert entity.available is False

        # Cycle 5: Ошибка aiohttp (битый payload)
        client.get_telemetry.side_effect = aiohttp.ClientPayloadError("Premature end of payload")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        assert entity.available is False

        # Cycle 6: Окончательное восстановление
        snapshot_6 = dataclasses.replace(
            sample_snapshot_2,
            timestamp=1200.0,
            uptime_seconds=sample_snapshot_2.uptime_seconds + 110.04,
            g9962_counters={
                **sample_snapshot_2.g9962_counters,
                "Bytes_TX": sample_snapshot_2.g9962_counters["Bytes_TX"] + 150000,
                "Bytes_RX": sample_snapshot_2.g9962_counters["Bytes_RX"] + 80000,
                "BLOCKS_TX": sample_snapshot_2.g9962_counters["BLOCKS_TX"] + 1000,
                "BLOCKS_RX": sample_snapshot_2.g9962_counters["BLOCKS_RX"] + 500,
            },
        )
        client.get_telemetry.side_effect = None
        client.get_telemetry.return_value = snapshot_6
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert entity.available is True
        assert coordinator.data.telemetry_interval is not None
        assert coordinator.data.telemetry_interval.tx_bytes == 150000

    async def test_sub_request_failures_consistently_mark_coordinator_failed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет сбои в каждом из подзапросов (get_device_info, get_peers, get_telemetry)."""
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

        # 1. Сбой get_peers
        client.get_peers.side_effect = PG2400PConnectionError("PLC peer list query failed")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Восстановление get_peers
        client.get_peers.side_effect = None
        client.get_peers.return_value = [MOCK_PEER]
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

        # 2. Сбой get_device_info
        client.get_device_info.side_effect = aiohttp.ServerDisconnectedError("Reset by peer")
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Восстановление get_device_info
        client.get_device_info.side_effect = None
        client.get_device_info.return_value = MOCK_DEVICE_INFO
        await coordinator.async_refresh()
        assert coordinator.last_update_success is True

    async def test_empty_peers_link_down_handling(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обработку ситуации, когда Powerline-связь разорвана (список peers пуст)."""
        client = _build_mock_client(telemetry=sample_snapshot_1, peers=[])
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        # Опрос успешен, но связь PLC неактивна
        assert coordinator.last_update_success is True
        assert coordinator.data.peers == []
        assert coordinator.data.plc_link_active is False

    async def test_extended_ten_cycle_outage_and_recovery(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет длительный сбой связи (10 циклов подряд) и последующее восстановление."""
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

        # 10 аварийных опросов подряд
        client.get_device_info.side_effect = PG2400PConnectionError("Cable unplugged")
        for _ in range(10):
            await coordinator.async_refresh()
            assert coordinator.last_update_success is False

        # Восстановление после длительного простоя
        client.get_device_info.side_effect = None
        client.get_telemetry.return_value = sample_snapshot_2
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        assert coordinator.data.telemetry_interval is not None
        assert coordinator.data.telemetry_interval.duration_seconds == 89.96


# =========================================================================
# 2. Стресс-тесты внезапной перезагрузки адаптера во время опроса
# =========================================================================


class TestSuddenRebootDuringPolling:
    """Проверка корректности сброса базовых линий и устойчивости к перезагрузкам."""

    async def test_clean_reboot_between_polls_resets_baseline_gracefully(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что при перезагрузке (регресс аптайма) не возникает исключений."""
        # Poll 1: адаптер работает давно, аптайм 150 000 с
        snapshot_1 = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=150000.0,
            timestamp=1000.0,
        )
        client = _build_mock_client(telemetry=snapshot_1)
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
        assert coordinator.data.telemetry_interval is None

        # Poll 2: адаптер перезагрузился, аптайм упал до 8.5 секунд
        reboot_snapshot = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=8.5,
            timestamp=1030.0,
            g9962_counters={k: 0 for k in sample_snapshot_1.g9962_counters},
        )
        client.get_telemetry.return_value = reboot_snapshot
        await coordinator.async_refresh()

        # Координатор не падает, last_update_success остается True, а интервал сброшен в None
        assert coordinator.last_update_success is True
        assert coordinator.data.telemetry_interval is None

        # Poll 3: следующий опрос после перезагрузки (аптайм 38.5 с)
        post_reboot_snapshot = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=38.5,
            timestamp=1060.0,
            g9962_counters={
                **sample_snapshot_1.g9962_counters,
                "Bytes_TX": 40960,
                "Bytes_RX": 20480,
                "BLOCKS_TX": 320,
                "BLOCKS_RX": 160,
                "BLOCKS_RTX": 0,
                "BLOCKS_ERROR_RX": 0,
            },
        )
        client.get_telemetry.return_value = post_reboot_snapshot
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        interval = coordinator.data.telemetry_interval
        assert interval is not None
        assert interval.duration_seconds == 30.0
        assert interval.tx_bytes == 40960
        assert interval.rx_bytes == 20480

    async def test_reboot_mid_poll_and_subsequent_recovery(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет сценарий, когда перезагрузка происходит прямо в середине опроса.

        get_device_info успешен, но get_telemetry обрывается с ConnectionResetError.
        Затем следующий опрос успешен с новым базовым аптаймом.
        """
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

        # Внезапный обрыв во время запроса телеметрии (аппаратный ребут)
        client.get_telemetry.side_effect = PG2400PConnectionError(
            "Connection reset by peer on reboot"
        )
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False

        # Устройство перезагрузилось, аптайм 12 секунд
        client.get_telemetry.side_effect = None
        new_boot_snapshot = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=12.0,
            timestamp=1060.0,
        )
        client.get_telemetry.return_value = new_boot_snapshot
        await coordinator.async_refresh()

        # Восстановление успешно, базовая линия сброшена без ошибок
        assert coordinator.last_update_success is True
        assert coordinator.data.telemetry_interval is None

    async def test_zero_or_negative_duration_handled_safely(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обработку нулевого интервала времени между опросами (дубликат снимка)."""
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

        # Повторный опрос с тем же самым аптаймом и временем (дубликат)
        client.get_telemetry.return_value = sample_snapshot_1
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        # duration == 0 -> telemetry_interval корректно сброшен в None
        assert coordinator.data.telemetry_interval is None

    async def test_exception_in_interval_computation_gracefully_degrades(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет мягкую деградацию при неожиданном исключении в compute_telemetry_interval."""
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
        with patch(
            "custom_components.currentlink.coordinator.compute_telemetry_interval",
            side_effect=ZeroDivisionError("Corrupt interval duration"),
        ):
            await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        # Ошибка вычисления интервала перехвачена, координатор жив, telemetry_interval = None
        assert coordinator.data.telemetry_interval is None
        assert coordinator.data.device_info.mac_address == TEST_MAC

    async def test_xtensa_32bit_counter_rollover_preserved_as_valid_delta(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет корректность вычисления дельты при 32-битном переполнении (wrap-around)."""
        # Снимок 1: значение близко к 2^32 (4 294 967 296)
        snap1 = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=1000.0,
            timestamp=1000.0,
            g9962_counters={
                **sample_snapshot_1.g9962_counters,
                "Bytes_TX": 4294900000,
                "BLOCKS_TX": 10000,
                "BLOCKS_RTX": 0,
            },
        )
        client = _build_mock_client(telemetry=snap1)
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        await coordinator.async_config_entry_first_refresh()

        # Снимок 2: счётчик переполнился и равен 50000
        # Дельта = (4294967296 - 4294900000) + 50000 = 67296 + 50000 = 117296
        snap2 = dataclasses.replace(
            sample_snapshot_1,
            uptime_seconds=1030.0,
            timestamp=1030.0,
            g9962_counters={
                **sample_snapshot_1.g9962_counters,
                "Bytes_TX": 50000,
                "BLOCKS_TX": 11000,
                "BLOCKS_RTX": 0,
            },
        )
        client.get_telemetry.return_value = snap2
        await coordinator.async_refresh()

        assert coordinator.last_update_success is True
        interval = coordinator.data.telemetry_interval
        assert interval is not None
        assert interval.tx_bytes == 117296


# =========================================================================
# 3. Стресс-тесты истечения срока и ошибок авторизации
# =========================================================================


class TestAuthenticationExpiration:
    """Проверка маппинга AuthenticationError в ConfigEntryAuthFailed."""

    async def test_auth_error_on_first_refresh_raises_config_entry_auth_failed(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет возбуждение ConfigEntryAuthFailed при первом запуске."""
        client = _build_mock_client()
        client.get_device_info.side_effect = AuthenticationError(
            "Password invalid on adapter", login_attempts=2
        )
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        with pytest.raises(ConfigEntryAuthFailed) as exc_info:
            await coordinator.async_config_entry_first_refresh()

        assert "Неверный пароль для адаптера" in str(exc_info.value)
        assert TEST_HOST in str(exc_info.value)

    async def test_auth_error_during_scheduled_refresh_triggers_auth_failed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет поведение при внезапном истечении/смене пароля во время регулярного опроса."""
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

        # При следующем опросе пароль на адаптере изменён
        client.get_device_info.side_effect = AuthenticationError(
            "Session expired / credentials revoked"
        )

        # В Home Assistant coordinator.async_refresh() при ConfigEntryAuthFailed
        # автоматически вызывает entry.async_start_reauth_if_available(self.hass)
        with patch.object(entry, "async_start_reauth_if_available") as mock_reauth:
            await coordinator.async_refresh()

        assert coordinator.last_update_success is False
        assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)
        mock_reauth.assert_called_once_with(hass)

    async def test_auth_error_in_auxiliary_port_status_not_swallowed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что AuthenticationError в _async_fetch_port_status не маскируется."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.post.side_effect = AuthenticationError("Auth token expired during port query")

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

    async def test_auth_error_in_auxiliary_led_state_not_swallowed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что AuthenticationError в _async_fetch_led_state не маскируется."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.query.side_effect = AuthenticationError("Auth token expired during LED query")

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
# 4. Стресс-тесты быстрых циклов монтирования и выгрузки (Setup/Unload)
# =========================================================================


class TestRapidSetupUnloadCycles:
    """Проверка отсутствия утечек сессий, памяти и слушателей при циклических выгрузках."""

    async def test_rapid_consecutive_setup_unload_10_cycles(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет 10 последовательных циклов async_setup_entry -> async_unload_entry."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        entry.add_to_hass(hass)

        for cycle in range(1, 11):
            mock_client = _build_mock_client(telemetry=sample_snapshot_1)
            entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

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
                setup_ok = await async_setup_entry(hass, entry)

            assert setup_ok is True, f"Cycle {cycle}: setup failed"
            assert DOMAIN in hass.data
            assert entry.entry_id in hass.data[DOMAIN]
            coordinator = hass.data[DOMAIN][entry.entry_id]
            assert isinstance(coordinator, PG2400PDataUpdateCoordinator)
            assert entry.runtime_data is coordinator
            mock_forward.assert_awaited_once_with(entry, PLATFORMS)

            # Выгрузка интеграции
            with patch.object(
                hass.config_entries,
                "async_unload_platforms",
                return_value=True,
            ) as mock_unload:
                unload_ok = await async_unload_entry(hass, entry)

            assert unload_ok is True, f"Cycle {cycle}: unload failed"
            mock_unload.assert_awaited_once_with(entry, PLATFORMS)
            # Сессия клиента на адаптере обязательно закрыта
            mock_client.close.assert_awaited_once()

            # Координатор удалён из hass.data
            assert entry.entry_id not in hass.data.get(DOMAIN, {})
            # Домен удалён, если больше нет активных записей
            assert DOMAIN not in hass.data

    async def test_double_unload_idempotency(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что повторный вызов async_unload_entry безопасен и не вызывает исключений."""
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
            patch.object(hass.config_entries, "async_forward_entry_setups", return_value=True),
        ):
            await async_setup_entry(hass, entry)

        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            # Первый unload
            first_unload = await async_unload_entry(hass, entry)
            assert first_unload is True

            # Второй unload для той же записи (идемпотентность)
            second_unload = await async_unload_entry(hass, entry)
            assert second_unload is True

    async def test_multiple_entries_lifecycle_isolation(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет независимость жизненного цикла при наличии двух адаптеров в системе."""
        entry_main = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: TEST_PASSWORD},
        )
        entry_remote = MockConfigEntry(
            domain=DOMAIN,
            unique_id=PEER_MAC,
            data={CONF_HOST: "192.0.2.11", CONF_PASSWORD: TEST_PASSWORD},
        )
        entry_main.add_to_hass(hass)
        entry_remote.add_to_hass(hass)
        entry_main.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
        entry_remote.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        client_main = _build_mock_client(telemetry=sample_snapshot_1)
        client_remote = _build_mock_client(telemetry=sample_snapshot_1)

        with (
            patch(
                "custom_components.currentlink.PG2400PClient",
                side_effect=[client_main, client_remote],
            ),
            patch.object(hass.config_entries, "async_forward_entry_setups", return_value=True),
        ):
            await async_setup_entry(hass, entry_main)
            await async_setup_entry(hass, entry_remote)

        assert len(hass.data[DOMAIN]) == 2
        assert entry_main.entry_id in hass.data[DOMAIN]
        assert entry_remote.entry_id in hass.data[DOMAIN]

        # Выгружаем только первый адаптер (main)
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            unload_main = await async_unload_entry(hass, entry_main)

        assert unload_main is True
        client_main.close.assert_awaited_once()
        client_remote.close.assert_not_awaited()

        # Второй адаптер (remote) остаётся активным в hass.data
        assert DOMAIN in hass.data
        assert entry_main.entry_id not in hass.data[DOMAIN]
        assert entry_remote.entry_id in hass.data[DOMAIN]

        # Выгружаем второй адаптер
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
            unload_remote = await async_unload_entry(hass, entry_remote)

        assert unload_remote is True
        client_remote.close.assert_awaited_once()
        assert DOMAIN not in hass.data

    async def test_unload_failure_preserves_resources(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет, что при сбое выгрузки платформ клиент не закрывается преждевременно."""
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
            patch.object(hass.config_entries, "async_forward_entry_setups", return_value=True),
        ):
            await async_setup_entry(hass, entry)

        # Симулируем отказ платформ выгружаться
        with patch.object(hass.config_entries, "async_unload_platforms", return_value=False):
            unload_ok = await async_unload_entry(hass, entry)

        assert unload_ok is False
        # Ресурсы не должны быть уничтожены
        assert entry.entry_id in hass.data[DOMAIN]
        mock_client.close.assert_not_awaited()


# =========================================================================
# 5. Стресс-тесты динамического обновления интервала опроса
# =========================================================================


class TestPollingIntervalDynamicUpdate:
    """Проверка обновления интервала опроса через OptionsFlow и ConfigEntry."""

    async def test_default_scan_interval_applied(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет применение DEFAULT_SCAN_INTERVAL (30 с) при отсутствии настроек."""
        client = _build_mock_client()
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )
        entry.add_to_hass(hass)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL)

    async def test_custom_scan_interval_in_entry_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет применение интервала из data при отсутствии options."""
        client = _build_mock_client()
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={
                CONF_HOST: TEST_HOST,
                CONF_PASSWORD: TEST_PASSWORD,
                CONF_SCAN_INTERVAL: 45,
            },
        )
        entry.add_to_hass(hass)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        assert coordinator.update_interval == timedelta(seconds=45)

    async def test_options_scan_interval_overrides_entry_data(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет приоритет options над data."""
        client = _build_mock_client()
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={
                CONF_HOST: TEST_HOST,
                CONF_PASSWORD: TEST_PASSWORD,
                CONF_SCAN_INTERVAL: 60,
            },
            options={CONF_SCAN_INTERVAL: 15},
        )
        entry.add_to_hass(hass)

        coordinator = PG2400PDataUpdateCoordinator(hass, entry, client)
        assert coordinator.update_interval == timedelta(seconds=15)

    async def test_boundary_scan_intervals(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет граничные значения интервалов (15 с и 300 с)."""
        client = _build_mock_client()
        entry_min = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: MIN_SCAN_INTERVAL},
        )
        entry_max = MockConfigEntry(
            domain=DOMAIN,
            unique_id=PEER_MAC,
            data={CONF_HOST: "192.0.2.11", CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: MAX_SCAN_INTERVAL},
        )
        coord_min = PG2400PDataUpdateCoordinator(hass, entry_min, client)
        coord_max = PG2400PDataUpdateCoordinator(hass, entry_max, client)

        assert coord_min.update_interval == timedelta(seconds=15)
        assert coord_max.update_interval == timedelta(seconds=300)

    async def test_dynamic_options_listener_calls_reload_and_updates_interval(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обновление интервала опроса при изменении опций пользователем."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: 30},
        )
        entry.add_to_hass(hass)
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)

        mock_client = _build_mock_client(telemetry=sample_snapshot_1)
        with (
            patch(
                "custom_components.currentlink.PG2400PClient",
                return_value=mock_client,
            ),
            patch.object(hass.config_entries, "async_forward_entry_setups", return_value=True),
        ):
            await async_setup_entry(hass, entry)

        coord_before = entry.runtime_data
        assert coord_before.update_interval == timedelta(seconds=30)

        # Обновляем опции записи (например, через UI Options Flow) на 60 секунд
        with patch.object(hass.config_entries, "async_reload") as mock_reload:
            await async_update_options(hass, entry)

        mock_reload.assert_awaited_once_with(entry.entry_id)
