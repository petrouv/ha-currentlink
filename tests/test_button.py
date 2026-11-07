"""Тесты платформы button для интеграции TP-Link PG2400P."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.button import (
    DOMAIN as BUTTON_DOMAIN,
)
from homeassistant.components.button import (
    SERVICE_PRESS,
    ButtonDeviceClass,
)
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN, MANUFACTURER, MODEL
from custom_components.currentlink.errors import (
    AuthenticationError,
    PG2400PConnectionError,
)
from custom_components.currentlink.models import DeviceInfo, TelemetrySnapshot

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


def _build_mock_client() -> AsyncMock:
    """Создает мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = MOCK_DEVICE_INFO
    client.get_peers.return_value = []
    client.post.return_value = {"PORT1": "6", "PORT2": "0"}
    client.query.return_value = {LED_ENABLE_KEY: "1"}
    client.reboot.return_value = True
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


class TestButtonPlatform:
    """Тестирование платформы кнопок TP-Link PG2400P."""

    async def test_reboot_button_setup(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет регистрацию кнопки перезагрузки и её свойства."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        entity_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
        button_entries = [e for e in entries if e.domain == BUTTON_DOMAIN]
        assert len(button_entries) == 1

        button_entry = button_entries[0]
        assert button_entry.unique_id == f"{TEST_MAC}_reboot"
        assert button_entry.translation_key == "reboot"
        assert button_entry.entity_category == EntityCategory.CONFIG

        # Проверка Device Registry
        device_reg = dr.async_get(hass)
        device = device_reg.async_get_device_by_identifier(
            (DOMAIN, TEST_MAC), config_entry_id=entry.entry_id
        )
        assert device is not None
        assert device.manufacturer == MANUFACTURER
        assert device.model == MODEL
        assert device.sw_version == MOCK_DEVICE_INFO.fw_version
        assert device.hw_version == MOCK_DEVICE_INFO.hw_revision

        # Проверка состояния
        state = hass.states.get(button_entry.entity_id)
        assert state is not None
        assert state.attributes.get("device_class") == ButtonDeviceClass.RESTART

    async def test_reboot_button_press(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет успешный вызов команды перезагрузки."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
        client.reboot.assert_awaited_once()

    async def test_reboot_button_handles_connection_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обработку сетевой ошибки при перезагрузке."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.reboot.side_effect = PG2400PConnectionError("Device unreachable")
        await _setup_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

    async def test_reboot_button_handles_auth_error(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет обработку ошибки аутентификации при перезагрузке."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.reboot.side_effect = AuthenticationError("Auth token invalid")
        await _setup_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

    async def test_reboot_button_device_rejected(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет реакцию при отказе адаптера перезагрузиться (return False)."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.reboot.return_value = False
        await _setup_integration(hass, client)

        state_btn = next(s for s in hass.states.async_all(BUTTON_DOMAIN) if "reboot" in s.entity_id)
        entity_id = state_btn.entity_id

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                BUTTON_DOMAIN,
                SERVICE_PRESS,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
