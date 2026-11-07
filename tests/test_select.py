"""Тесты платформы select для интеграции TP-Link PG2400P."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_PASSWORD,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN
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


def _build_mock_client(settings: PowerlineSettings | None = None) -> AsyncMock:
    """Создает мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = MOCK_DEVICE_INFO
    client.get_peers.return_value = []
    client.post.return_value = {"PORT1": "6", "PORT2": "0", "ERROR": "000", "RESULT": "0"}
    client.query.return_value = {LED_ENABLE_KEY: "YES"}

    current_settings = settings or PowerlineSettings(
        phy_mode="MIMO",
        compatibility_mode="Full Power",
        qos_mode="Fair",
        xdsl_mitigation=True,
    )
    client.get_powerline_settings.return_value = current_settings

    async def _mock_set_phy_mode(mode: str) -> bool:
        client.get_powerline_settings.return_value = dataclasses.replace(
            client.get_powerline_settings.return_value, phy_mode=mode
        )
        return True

    async def _mock_set_compatibility_mode(mode: str) -> bool:
        client.get_powerline_settings.return_value = dataclasses.replace(
            client.get_powerline_settings.return_value, compatibility_mode=mode
        )
        return True

    async def _mock_set_qos_mode(mode: str) -> bool:
        client.get_powerline_settings.return_value = dataclasses.replace(
            client.get_powerline_settings.return_value, qos_mode=mode
        )
        return True

    client.set_phy_mode.side_effect = _mock_set_phy_mode
    client.set_compatibility_mode.side_effect = _mock_set_compatibility_mode
    client.set_qos_mode.side_effect = _mock_set_qos_mode
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


class TestSelectPlatform:
    """Тестирование платформы select TP-Link PG2400P."""

    async def test_select_entities_setup(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет регистрацию всех сущностей select в реестре."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        entity_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
        select_entries = [e for e in entries if e.domain == SELECT_DOMAIN]
        assert len(select_entries) == 3

        keys = {e.translation_key for e in select_entries}
        assert keys == {"phy_mode", "vdsl_compatibility_mode", "qos_mode"}

        for e in select_entries:
            assert e.entity_category == EntityCategory.CONFIG

        # Проверка начальных состояний
        s_phy = hass.states.get("select.plc_main_phy_mode")
        assert s_phy is not None
        assert s_phy.state == "MIMO"

        s_compat = hass.states.get("select.plc_main_vdsl_compatibility_mode")
        assert s_compat is not None
        assert s_compat.state == "Full Power"

        s_qos = hass.states.get("select.plc_main_qos_mode")
        assert s_qos is not None
        assert s_qos.state == "Fair"

    async def test_select_change_phy_mode(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет смену режима PHY."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_phy_mode"
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "SISO"},
            blocking=True,
        )

        client.set_phy_mode.assert_called_once_with("SISO")
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "SISO"

    async def test_select_change_compatibility_mode(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет смену профиля совместимости VDSL."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_vdsl_compatibility_mode"
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "VDSL 35b"},
            blocking=True,
        )

        client.set_compatibility_mode.assert_called_once_with("VDSL 35b")
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "VDSL 35b"

    async def test_select_change_qos_mode(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет смену профиля QoS."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_qos_mode"
        await hass.services.async_call(
            SELECT_DOMAIN,
            SERVICE_SELECT_OPTION,
            {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Gaming"},
            blocking=True,
        )

        client.set_qos_mode.assert_called_once_with("Gaming")
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "Gaming"

    async def test_select_invalid_option_raises(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверка отклонения некорректной опции."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_qos_mode"
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SELECT_DOMAIN,
                SERVICE_SELECT_OPTION,
                {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "InvalidMode"},
                blocking=True,
            )

    async def test_select_rejection_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет откат при отклонении команды адаптером (return False)."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_qos_mode.side_effect = None
        client.set_qos_mode.return_value = False
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_qos_mode"
        assert hass.states.get(entity_id).state == "Fair"

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SELECT_DOMAIN,
                SERVICE_SELECT_OPTION,
                {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Gaming"},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == "Fair"

    async def test_select_error_rollback(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет откат при ошибке связи."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        client.set_phy_mode.side_effect = PG2400PConnectionError("Device unreachable")
        await _setup_integration(hass, client)

        entity_id = "select.plc_main_phy_mode"
        assert hass.states.get(entity_id).state == "MIMO"

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                SELECT_DOMAIN,
                SERVICE_SELECT_OPTION,
                {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "SISO"},
                blocking=True,
            )

        assert hass.states.get(entity_id).state == "MIMO"

    async def test_select_unavailable_when_settings_none(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет недоступность селекторов, если настройки не получены."""
        client = _build_mock_client()
        client.get_telemetry.return_value = sample_snapshot_1
        entry = await _setup_integration(hass, client)

        coordinator = entry.runtime_data
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, settings=None))
        await hass.async_block_till_done()

        assert hass.states.get("select.plc_main_phy_mode").state == "unavailable"
        assert hass.states.get("select.plc_main_vdsl_compatibility_mode").state == "unavailable"
        assert hass.states.get("select.plc_main_qos_mode").state == "unavailable"
