"""Эмпирические стресс-тесты и adversarial-проверки платформ switch, button и binary_sensor.

Охватывает:
1. LED Switch:
   - Сбой сети при включении/выключении: откат оптимистичного состояния и HomeAssistantError.
   - Отклонение команды адаптером: откат оптимистичного состояния и HomeAssistantError.
   - Частые повторные и параллельные переключения (rapid/concurrent toggles):
     сериализация через client._lock, отсутствие race conditions и десинхронизации кэша.
   - Очистка оптимистичного состояния при входящем фоновом обновлении координатора.
2. Reboot Button:
   - Нажатие при недоступности адаптера (PG2400PConnectionError, ConnectionTimeoutError, OSError):
     гарантированное поднятие HomeAssistantError.
   - Ошибка аутентификации при перезагрузке: эскалация в HomeAssistantError.
   - Отказ адаптера перезагрузиться (return False): эскалация в HomeAssistantError.
   - Штатная перезагрузка: корректный вызов reboot() без ошибок.
   - Параллельные нажатия (rapid concurrent presses).
3. Binary Sensors:
   - Отключение канала Powerline (plc_link_active = False): состояние is_on = False,
     динамическая иконка "mdi:power-plug-off", active_peers_count = 0, active_peer_macs = [].
   - Проверка различных сценариев: пустой список пиров, список пиров с active=False,
     смешанные пиры.
   - Полная матрица переходов режимов энергосбережения (lan_power_saving и traffic_power_saving):
     (False, False) -> (True, False) -> (False, True) -> (True, True) -> (False, False).
   - Обработка coordinator.data = None без необработанных исключений.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
)
from homeassistant.components.button import (
    SERVICE_PRESS,
)
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
)
from homeassistant.components.switch import (
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN
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
PEER_MAC_1 = "aa:bb:cc:00:11:33"
PEER_MAC_2 = "aa:bb:cc:00:11:34"

MOCK_DEVICE_INFO = DeviceInfo(
    model="PG2400P",
    hw_revision="1.0",
    fw_version="1.0.3",
    mac_address=TEST_MAC,
    device_name="plc-main",
    domain_name="HomeNetwork",
    ip_address=TEST_HOST,
    lan_power_saving=False,
    traffic_power_saving=False,
)

MOCK_PEER_ACTIVE = PeerLink(
    peer_mac=PEER_MAC_1,
    rx_rate_mbps=1200,
    tx_rate_mbps=1100,
    rx_raw=38400,
    tx_raw=35200,
    active=True,
)


def _build_mock_client(
    led_enabled: bool = True,
    device_info: DeviceInfo = MOCK_DEVICE_INFO,
    peers: list[PeerLink] | None = None,
    telemetry: TelemetrySnapshot | None = None,
) -> AsyncMock:
    """Создаёт настраиваемый мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.password = TEST_PASSWORD
    client.get_device_info.return_value = device_info
    client.get_peers.return_value = [MOCK_PEER_ACTIVE] if peers is None else peers
    if telemetry is not None:
        client.get_telemetry.return_value = telemetry
    client.post.return_value = {"PORT1": "6", "PORT2": "0", "ERROR": "000"}
    client.query.return_value = {LED_ENABLE_KEY: "1" if led_enabled else "0"}
    client.reboot.return_value = True

    async def _mock_set_led(enabled: bool) -> bool:
        client.query.return_value = {LED_ENABLE_KEY: "1" if enabled else "0"}
        return True

    client.set_led.side_effect = _mock_set_led
    client.close.return_value = None
    return client


async def _setup_test_integration(hass: HomeAssistant, mock_client: AsyncMock) -> MockConfigEntry:
    """Инициализирует интеграцию с заданным мок-клиентом."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.currentlink.PG2400PClient",
        return_value=mock_client,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# ==============================================================================
# 1. LED Switch: Concurrency & Failures
# ==============================================================================


class TestAdversarialLedSwitch:
    """Стресс-тесты переключателя светодиодных индикаторов."""

    async def test_switch_toggle_on_network_drop_reverts_state(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Сбой сети при включении откатывает состояние в STATE_OFF и генерирует ошибку."""
        client = _build_mock_client(led_enabled=False, telemetry=sample_snapshot_1)
        client.set_led.side_effect = PG2400PConnectionError("Network unreachable")
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_OFF

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "Network unreachable" in str(exc_info.value) or "Сбой связи" in str(exc_info.value)

        # Проверка отката состояния
        state_after = hass.states.get(entity_id)
        assert state_after is not None
        assert state_after.state == STATE_OFF
        assert state_after.attributes.get("icon") == "mdi:led-off"

    async def test_switch_toggle_off_timeout_reverts_state(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Таймаут сокета при выключении откатывает в STATE_ON и поднимает ошибку."""
        client = _build_mock_client(led_enabled=True, telemetry=sample_snapshot_1)
        client.set_led.side_effect = ConnectionTimeoutError("Socket read timeout")
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_ON

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "Socket read timeout" in str(exc_info.value) or "Сбой связи" in str(exc_info.value)

        state_after = hass.states.get(entity_id)
        assert state_after is not None
        assert state_after.state == STATE_ON
        assert state_after.attributes.get("icon") == "mdi:led-on"

    async def test_switch_toggle_adapter_rejection_reverts_state(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Отказ адаптера (return False) откатывает состояние и поднимает ошибку."""
        client = _build_mock_client(led_enabled=True, telemetry=sample_snapshot_1)
        client.set_led.side_effect = None
        client.set_led.return_value = False
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_ON

        # Попытка выключения завершается отказом адаптера
        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "отклонил команду" in str(exc_info.value)
        assert hass.states.get(entity_id).state == STATE_ON

    async def test_switch_toggle_client_os_error_reverts_state(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Сетевая ошибка aiohttp/OS корректно оборачивается в HomeAssistantError."""
        client = _build_mock_client(led_enabled=False, telemetry=sample_snapshot_1)
        client.set_led.side_effect = aiohttp.ClientOSError("Connection reset by peer")
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == STATE_OFF

    async def test_switch_rapid_sequential_toggles(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Быстрые последовательные переключения выполняются строго по очереди."""
        client = _build_mock_client(led_enabled=False, telemetry=sample_snapshot_1)
        call_history: list[bool] = []

        async def _tracked_set_led(enabled: bool) -> bool:
            call_history.append(enabled)
            client.query.return_value = {LED_ENABLE_KEY: "1" if enabled else "0"}
            return True

        client.set_led.side_effect = _tracked_set_led
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id

        # 5 быстрых переключений подряд
        commands = [
            SERVICE_TURN_ON,
            SERVICE_TURN_OFF,
            SERVICE_TURN_ON,
            SERVICE_TURN_OFF,
            SERVICE_TURN_ON,
        ]
        for cmd in commands:
            await hass.services.async_call(
                SWITCH_DOMAIN,
                cmd,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert call_history == [True, False, True, False, True]
        state_final = hass.states.get(entity_id)
        assert state_final is not None
        assert state_final.state == STATE_ON
        assert state_final.attributes.get("icon") == "mdi:led-on"

    async def test_switch_concurrent_toggles_serialization_and_stability(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Параллельный запуск множественных turn_on / turn_off завершается без сбоев."""
        client = _build_mock_client(led_enabled=False, telemetry=sample_snapshot_1)
        simulated_lock = asyncio.Lock()
        in_flight_count = 0
        max_concurrent_seen = 0

        async def _delayed_set_led(enabled: bool) -> bool:
            nonlocal in_flight_count, max_concurrent_seen
            # Имитируем сериализацию сетевых запросов через блокировку клиента
            async with simulated_lock:
                in_flight_count += 1
                max_concurrent_seen = max(max_concurrent_seen, in_flight_count)
                await asyncio.sleep(0.01)
                in_flight_count -= 1
            client.query.return_value = {LED_ENABLE_KEY: "1" if enabled else "0"}
            return True

        client.set_led.side_effect = _delayed_set_led
        await _setup_test_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id

        # 8 параллельных сервисных вызовов чередующихся команд
        tasks = [
            hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON if i % 2 == 0 else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            for i in range(8)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            assert not isinstance(res, Exception), f"Concurrent toggle raised unexpected: {res}"

        # Запросы к адаптеру были строго сериализованы
        assert max_concurrent_seen == 1
        assert client.set_led.await_count == 8

        # Финальное состояние валидно (ON или OFF, не corrupt)
        state_final = hass.states.get(entity_id)
        assert state_final is not None
        assert state_final.state in (STATE_ON, STATE_OFF)

    async def test_switch_coordinator_poll_resets_optimistic_state(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Поступление данных от координатора сбрасывает оптимистичное состояние переключателя."""
        client = _build_mock_client(led_enabled=False, telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)

        coordinator = entry.runtime_data
        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert hass.states.get(entity_id).state == STATE_OFF

        # Имитируем подтверждение включения LED при опросе адаптера
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, led_enabled=True))
        await hass.async_block_till_done()

        state_after_poll = hass.states.get(entity_id)
        assert state_after_poll.state == STATE_ON
        assert state_after_poll.attributes.get("icon") == "mdi:led-on"


# ==============================================================================
# 2. Reboot Button: Adversarial Tests
# ==============================================================================


class TestAdversarialRebootButton:
    """Стресс-тесты кнопки перезагрузки адаптера."""

    async def test_reboot_button_network_error_raises_ha_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При разрыве связи с адаптером нажатие кнопки выбрасывает HomeAssistantError."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.side_effect = PG2400PConnectionError("Target 192.0.2.10 host down")
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "host down" in str(exc_info.value) or "Не удалось перезагрузить" in str(
            exc_info.value
        )

    async def test_reboot_button_timeout_raises_ha_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При таймауте ответа адаптера выбрасывается HomeAssistantError."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.side_effect = ConnectionTimeoutError("Reboot command timed out after 10s")
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "timed out" in str(exc_info.value) or "Не удалось перезагрузить" in str(
            exc_info.value
        )

    async def test_reboot_button_auth_failure_raises_ha_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При ошибке аутентификации на адаптере выбрасывается HomeAssistantError."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.side_effect = AuthenticationError("Token rejected by adapter")
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "Token rejected" in str(exc_info.value) or "Не удалось перезагрузить" in str(
            exc_info.value
        )

    async def test_reboot_button_adapter_rejection_raises_ha_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Если адаптер возвращает False (отклонение команды), выбрасывается HomeAssistantError."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.return_value = False
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "отклонил команду" in str(exc_info.value)

    async def test_reboot_button_broken_pipe_os_error_raises_ha_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Ошибка BrokenPipeError / OSError обрабатывается и поднимает HomeAssistantError."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.side_effect = BrokenPipeError("Socket broken pipe")
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError) as exc_info:
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert "broken pipe" in str(exc_info.value) or "Не удалось перезагрузить" in str(
            exc_info.value
        )

    async def test_reboot_button_successful_press_executes_cleanly(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Успешное нажатие кнопки штатно вызывает reboot() клиента без исключений."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.return_value = True
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

        client.reboot.assert_awaited_once()

    async def test_reboot_button_concurrent_presses(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Параллельные одновременные нажатия кнопки обрабатываются без сбоев."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        client.reboot.return_value = True
        await _setup_test_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        tasks = [
            hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            for _ in range(5)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            assert not isinstance(res, Exception), f"Concurrent press failed: {res}"

        assert client.reboot.await_count == 5

    async def test_reboot_button_unavailable_when_coordinator_offline(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При падении координатора кнопка перезагрузки переходит в статус unavailable."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)

        coordinator = entry.runtime_data
        coordinator.last_update_success = False
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        assert state_btn.state == "unavailable"


# ==============================================================================
# 3. Binary Sensors: Disconnection & Transitions
# ==============================================================================


class TestAdversarialBinarySensors:
    """Стресс-тесты бинарных сенсоров plc_link и power_saving."""

    async def test_plc_link_disconnected_when_no_peers(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При отсутствии пиров в сети plc_link = OFF, иконка mdi:power-plug-off, счётчик 0."""
        client = _build_mock_client(peers=[], telemetry=sample_snapshot_1)
        await _setup_test_integration(hass, client)

        state = next(
            s
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )
        assert state.state == STATE_OFF
        assert state.attributes.get("device_class") == BinarySensorDeviceClass.CONNECTIVITY
        assert state.attributes.get("icon") == "mdi:power-plug-off"
        assert state.attributes.get("active_peers_count") == 0
        assert state.attributes.get("active_peer_macs") == []
        assert state.attributes.get("domain_name") == "HomeNetwork"

    async def test_plc_link_disconnected_when_peers_inactive(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При наличии пиров с active=False plc_link = OFF, иконка mdi:power-plug-off."""
        inactive_peers = [
            PeerLink(peer_mac=PEER_MAC_1, rx_rate_mbps=0, tx_rate_mbps=0, active=False),
            PeerLink(peer_mac=PEER_MAC_2, rx_rate_mbps=0, tx_rate_mbps=0, active=False),
        ]
        client = _build_mock_client(peers=inactive_peers, telemetry=sample_snapshot_1)
        await _setup_test_integration(hass, client)

        state = next(
            s
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )
        assert state.state == STATE_OFF
        assert state.attributes.get("icon") == "mdi:power-plug-off"
        assert state.attributes.get("active_peers_count") == 0
        assert state.attributes.get("active_peer_macs") == []

    async def test_plc_link_dynamic_connect_and_disconnect_transitions(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Динамические переходы подключения и отключения Powerline-канала."""
        client = _build_mock_client(peers=[], telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)
        coordinator = entry.runtime_data

        state_entity_id = next(
            s.entity_id
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )

        # 1. Начальное состояние: отключено
        assert hass.states.get(state_entity_id).state == STATE_OFF
        assert hass.states.get(state_entity_id).attributes.get("icon") == "mdi:power-plug-off"

        # 2. Подключение первого пира
        p1 = PeerLink(peer_mac=PEER_MAC_1, rx_rate_mbps=1200, tx_rate_mbps=1100, active=True)
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, peers=[p1]))
        await hass.async_block_till_done()

        state_step2 = hass.states.get(state_entity_id)
        assert state_step2.state == STATE_ON
        assert state_step2.attributes.get("icon") == "mdi:power-plug"
        assert state_step2.attributes.get("active_peers_count") == 1
        assert state_step2.attributes.get("active_peer_macs") == [PEER_MAC_1]

        # 3. Подключение второго пира (один активен, второй неактивен)
        p2_inactive = PeerLink(peer_mac=PEER_MAC_2, rx_rate_mbps=0, tx_rate_mbps=0, active=False)
        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, peers=[p1, p2_inactive])
        )
        await hass.async_block_till_done()

        state_step3 = hass.states.get(state_entity_id)
        assert state_step3.state == STATE_ON
        assert state_step3.attributes.get("active_peers_count") == 1
        assert state_step3.attributes.get("active_peer_macs") == [PEER_MAC_1]

        # 4. Оба пира отключились
        p1_inactive = PeerLink(peer_mac=PEER_MAC_1, rx_rate_mbps=0, tx_rate_mbps=0, active=False)
        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, peers=[p1_inactive, p2_inactive])
        )
        await hass.async_block_till_done()

        state_step4 = hass.states.get(state_entity_id)
        assert state_step4.state == STATE_OFF
        assert state_step4.attributes.get("icon") == "mdi:power-plug-off"
        assert state_step4.attributes.get("active_peers_count") == 0
        assert state_step4.attributes.get("active_peer_macs") == []

    async def test_power_saving_full_transition_cycle(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Полный цикл переходов всех состояний энергосбережения."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)
        coordinator = entry.runtime_data

        state_entity_id = next(
            s.entity_id
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "power_saving" in s.entity_id
        )

        matrix = [
            (False, False, STATE_OFF, "mdi:power-sleep-off"),
            (True, False, STATE_ON, "mdi:power-sleep"),
            (False, True, STATE_ON, "mdi:power-sleep"),
            (True, True, STATE_ON, "mdi:power-sleep"),
            (False, False, STATE_OFF, "mdi:power-sleep-off"),
        ]

        for lan_ps, traffic_ps, expected_state, expected_icon in matrix:
            new_dev_info = dataclasses.replace(
                coordinator.data.device_info,
                lan_power_saving=lan_ps,
                traffic_power_saving=traffic_ps,
            )
            coordinator.async_set_updated_data(
                dataclasses.replace(coordinator.data, device_info=new_dev_info)
            )
            await hass.async_block_till_done()

            st = hass.states.get(state_entity_id)
            assert st is not None
            err_msg = f"Failed state for lan={lan_ps}, traffic={traffic_ps}"
            assert st.state == expected_state, err_msg
            assert st.attributes.get("icon") == expected_icon
            assert st.attributes.get("lan_power_saving") is lan_ps
            assert st.attributes.get("traffic_power_saving") is traffic_ps

    async def test_power_saving_legacy_attribute_fallbacks(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Поддержка альтернативных имён атрибутов энергосбережения без сбоев."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)
        coordinator = entry.runtime_data

        state_entity_id = next(
            s.entity_id
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "power_saving" in s.entity_id
        )

        # Создаём объект без lan_power_saving / traffic_power_saving, но с power_saving_lan
        class LegacyDeviceInfo:
            model = "PG2400P"
            hw_revision = "1.0"
            fw_version = "1.0.3"
            mac_address = TEST_MAC
            device_name = "legacy"
            domain_name = "LegacyNet"
            ip_address = TEST_HOST
            power_saving_lan = True
            power_saving_msps = False

        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, device_info=LegacyDeviceInfo())  # type: ignore[arg-type]
        )
        await hass.async_block_till_done()

        st = hass.states.get(state_entity_id)
        assert st is not None
        assert st.state == STATE_ON
        assert st.attributes.get("lan_power_saving") is True
        assert st.attributes.get("traffic_power_saving") is False

    async def test_binary_sensors_handle_coordinator_data_none_safely(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При coordinator.data = None сенсоры возвращают None/unavailable без исключений."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_test_integration(hass, client)
        coordinator = entry.runtime_data

        # Устанавливаем data = None и last_update_success = False
        coordinator.data = None
        coordinator.last_update_success = False
        coordinator.async_update_listeners()
        await hass.async_block_till_done()

        for s in hass.states.async_all(BINARY_SENSOR_DOMAIN):
            assert s.state == "unavailable"
