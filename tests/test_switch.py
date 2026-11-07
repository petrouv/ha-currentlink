"""Тесты платформы switch для интеграции TP-Link PG2400P."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN, MANUFACTURER, MODEL
from custom_components.currentlink.errors import PG2400PConnectionError
from custom_components.currentlink.models import (
    DeviceInfo,
    PowerlineSettings,
    TelemetrySnapshot,
)

TEST_HOST = "192.0.2.10"
TEST_PASSWORD = "secret_password"
TEST_MAC = "aa:bb:cc:00:11:22"

MOCK_DEVICE_INFO = DeviceInfo(
    model="PG2400P",
    hw_revision="1.0",
    fw_version="1.0.3",
    mac_address=TEST_MAC,
    device_name="plc-main",
    domain_name="HomeNetwork",
    ip_address=TEST_HOST,
)


def _build_mock_client(led_enabled: bool = True) -> AsyncMock:
    """Создает мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = MOCK_DEVICE_INFO
    client.get_peers.return_value = []
    client.post.return_value = {"PORT1": "6", "PORT2": "0", "ERROR": "000"}
    client.query.return_value = {LED_ENABLE_KEY: "YES" if led_enabled else "NO"}
    client.get_powerline_settings.return_value = PowerlineSettings(
        phy_mode="MIMO",
        compatibility_mode="Full Power",
        qos_mode="Fair",
        xdsl_mitigation=True,
    )

    async def _mock_set_compat(enabled: bool) -> bool:
        client.get_powerline_settings.return_value = dataclasses.replace(
            client.get_powerline_settings.return_value, xdsl_mitigation=enabled
        )
        return True

    client.set_automatic_compatibility.side_effect = _mock_set_compat

    async def _mock_set_led(enabled: bool) -> bool:
        client.query.return_value = {LED_ENABLE_KEY: "YES" if enabled else "NO"}
        return True

    client.set_led.side_effect = _mock_set_led
    client.close.return_value = None
    return client


async def _setup_integration(hass: HomeAssistant, mock_client: AsyncMock) -> MockConfigEntry:
    """Настраивает интеграцию с мок-клиентом."""
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


class TestLedSwitchPlatform:
    """Тестирование переключателя светодиодов TP-Link PG2400P."""

    async def test_led_switch_setup(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет регистрацию переключателя и привязку к устройству."""
        client = _build_mock_client(led_enabled=True)
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        entity_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
        switch_entries = [e for e in entries if e.domain == SWITCH_DOMAIN]
        assert len(switch_entries) == 2

        switch_entry = next(e for e in switch_entries if e.translation_key == "led")
        assert switch_entry.unique_id == f"{TEST_MAC}_led"
        assert switch_entry.translation_key == "led"
        assert switch_entry.entity_category == EntityCategory.CONFIG

        compat_entry = next(
            e for e in switch_entries if e.translation_key == "automatic_compatibility"
        )
        assert compat_entry.unique_id == f"{TEST_MAC}_automatic_compatibility"
        assert compat_entry.translation_key == "automatic_compatibility"
        assert compat_entry.entity_category == EntityCategory.CONFIG

        # Проверка Device Registry
        device_reg = dr.async_get(hass)
        device = device_reg.async_get_device_by_identifier(
            (DOMAIN, TEST_MAC), config_entry_id=entry.entry_id
        )
        assert device is not None
        assert device.manufacturer == MANUFACTURER
        assert device.model == MODEL

        # Проверка состояния
        state = hass.states.get(switch_entry.entity_id)
        assert state is not None
        assert state.state == STATE_ON
        assert state.attributes.get("device_class") == SwitchDeviceClass.SWITCH
        assert state.attributes.get("icon") == "mdi:led-on"

    async def test_led_switch_turn_on_and_off(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет корректное включение и выключение светодиодов."""
        client = _build_mock_client(led_enabled=False)
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_OFF
        assert state_led.attributes.get("icon") == "mdi:led-off"

        # Включение светодиодов
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        client.set_led.assert_awaited_with(True)
        state_after_on = hass.states.get(entity_id)
        assert state_after_on.state == STATE_ON
        assert state_after_on.attributes.get("icon") == "mdi:led-on"

        # Выключение светодиодов
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        client.set_led.assert_awaited_with(False)
        state_after_off = hass.states.get(entity_id)
        assert state_after_off.state == STATE_OFF
        assert state_after_off.attributes.get("icon") == "mdi:led-off"

    async def test_led_switch_error_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет оптимистичный откат состояния при сетевой ошибке."""
        client = _build_mock_client(led_enabled=False)
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_led.side_effect = PG2400PConnectionError("Device unreachable")
        await _setup_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_OFF

        # Сбой при попытке включения -> откат в STATE_OFF
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == STATE_OFF

    async def test_led_switch_rejection_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет откат при отклонении команды адаптером (return False)."""
        client = _build_mock_client(led_enabled=True)
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_led.side_effect = None
        client.set_led.return_value = False
        await _setup_integration(hass, client)

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        entity_id = state_led.entity_id
        assert state_led.state == STATE_ON

        # Сбой при попытке выключения -> откат в STATE_ON
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == STATE_ON

    async def test_led_switch_unavailable_when_unsupported(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет недоступность переключателя при отсутствии поддержки LED."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        coordinator = entry.runtime_data
        # Имитируем прошивку без поддержки LED
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, led_enabled=None))
        await hass.async_block_till_done()

        state_led = next(s for s in hass.states.async_all(SWITCH_DOMAIN) if "led" in s.entity_id)
        assert state_led.state == "unavailable"

    async def test_auto_compat_switch_turn_on_and_off(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет переключение автоматической совместимости."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.get_powerline_settings.return_value = PowerlineSettings(
            phy_mode="MIMO",
            compatibility_mode="Full Power",
            qos_mode="Fair",
            xdsl_mitigation=False,
        )
        await _setup_integration(hass, client)

        entity_id = "switch.plc_main_automatic_compatibility"
        assert hass.states.get(entity_id).state == STATE_OFF

        # Включение
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        client.set_automatic_compatibility.assert_awaited_with(True)
        assert hass.states.get(entity_id).state == STATE_ON

        # Выключение
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        client.set_automatic_compatibility.assert_awaited_with(False)
        assert hass.states.get(entity_id).state == STATE_OFF

    async def test_auto_compat_switch_error_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет откат при ошибке связи при переключении авто-совместимости."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_automatic_compatibility.side_effect = PG2400PConnectionError(
            "Device unreachable"
        )
        await _setup_integration(hass, client)

        entity_id = "switch.plc_main_automatic_compatibility"
        assert hass.states.get(entity_id).state == STATE_ON

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == STATE_ON

    async def test_auto_compat_switch_rejection_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет откат при отклонении устройством смены авто-совместимости."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_automatic_compatibility.side_effect = None
        client.set_automatic_compatibility.return_value = False
        await _setup_integration(hass, client)

        entity_id = "switch.plc_main_automatic_compatibility"
        assert hass.states.get(entity_id).state == STATE_ON

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == STATE_ON

    async def test_auto_compat_switch_unavailable_when_settings_none(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет недоступность переключателя авто-совместимости при отсутствии настроек."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        coordinator = entry.runtime_data
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, settings=None))
        await hass.async_block_till_done()

        entity_id = "switch.plc_main_automatic_compatibility"
        assert hass.states.get(entity_id).state == "unavailable"
