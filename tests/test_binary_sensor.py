"""Тесты платформы binary_sensor для интеграции TP-Link PG2400P."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock, patch

from homeassistant.components.binary_sensor import (
    DOMAIN as BINARY_SENSOR_DOMAIN,
)
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN, MANUFACTURER, MODEL
from custom_components.currentlink.models import (
    DeviceInfo,
    PeerLink,
    PowerlineSettings,
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
    lan_power_saving=False,
    traffic_power_saving=False,
)

MOCK_PEER = PeerLink(
    peer_mac=PEER_MAC,
    rx_rate_mbps=1254,
    tx_rate_mbps=1180,
    rx_raw=40144,
    tx_raw=37768,
    active=True,
)


def _build_mock_client(
    device_info: DeviceInfo = MOCK_DEVICE_INFO,
    peers: list[PeerLink] | None = None,
    telemetry: TelemetrySnapshot | None = None,
) -> AsyncMock:
    """Создает мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = device_info
    client.get_peers.return_value = [MOCK_PEER] if peers is None else peers
    if telemetry is not None:
        client.get_telemetry.return_value = telemetry
    client.post.return_value = {"PORT1": "6", "PORT2": "0"}
    client.query.return_value = {LED_ENABLE_KEY: "1"}
    client.get_powerline_settings.return_value = PowerlineSettings(
        phy_mode="MIMO",
        compatibility_mode="Full Power",
        qos_mode="Fair",
        xdsl_mitigation=True,
    )
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


class TestBinarySensorPlatform:
    """Тестирование бинарных сенсоров TP-Link PG2400P."""

    async def test_binary_sensors_setup(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет регистрацию бинарных сенсоров и привязку к Device Registry."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)

        entity_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(entity_reg, entry.entry_id)
        bin_entries = [e for e in entries if e.domain == BINARY_SENSOR_DOMAIN]
        assert len(bin_entries) == 3

        unique_ids = {e.unique_id for e in bin_entries}
        assert f"{TEST_MAC}_plc_link" in unique_ids
        assert f"{TEST_MAC}_power_saving" in unique_ids
        assert f"{TEST_MAC}_xdsl_mitigation" in unique_ids

        # Проверка категорий и классов
        plc_entry = next(e for e in bin_entries if e.translation_key == "plc_link")
        assert plc_entry.entity_category is None

        ps_entry = next(e for e in bin_entries if e.translation_key == "power_saving")
        assert ps_entry.entity_category == EntityCategory.DIAGNOSTIC

        # Проверка Device Registry
        device_reg = dr.async_get(hass)
        device = device_reg.async_get_device_by_identifier(
            (DOMAIN, TEST_MAC), config_entry_id=entry.entry_id
        )
        assert device is not None
        assert device.manufacturer == MANUFACTURER
        assert device.model == MODEL

    async def test_plc_link_on_when_peer_active(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет состояние plc_link = ON при активном пире."""
        client = _build_mock_client(peers=[MOCK_PEER], telemetry=sample_snapshot_1)
        await _setup_integration(hass, client)

        state = next(
            s
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )
        assert state.state == STATE_ON
        assert state.attributes.get("device_class") == BinarySensorDeviceClass.CONNECTIVITY
        assert state.attributes.get("icon") == "mdi:power-plug"
        assert state.attributes.get("active_peers_count") == 1
        assert state.attributes.get("active_peer_macs") == [PEER_MAC]
        assert state.attributes.get("domain_name") == "HomeNetwork"

    async def test_plc_link_off_when_no_peers(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет состояние plc_link = OFF при отсутствии пиров."""
        client = _build_mock_client(peers=[], telemetry=sample_snapshot_1)
        await _setup_integration(hass, client)

        state = next(
            s
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )
        assert state.state == STATE_OFF
        assert state.attributes.get("icon") == "mdi:power-plug-off"
        assert state.attributes.get("active_peers_count") == 0
        assert state.attributes.get("active_peer_macs") == []

    async def test_plc_link_off_when_peer_inactive(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет состояние plc_link = OFF при неактивном пире (active=False)."""
        inactive_peer = PeerLink(
            peer_mac=PEER_MAC,
            rx_rate_mbps=0,
            tx_rate_mbps=0,
            active=False,
        )
        client = _build_mock_client(peers=[inactive_peer], telemetry=sample_snapshot_1)
        await _setup_integration(hass, client)

        state = next(
            s
            for s in hass.states.async_all(BINARY_SENSOR_DOMAIN)
            if "plc_link" in s.entity_id or "powerline_link" in s.entity_id
        )
        assert state.state == STATE_OFF
        assert state.attributes.get("icon") == "mdi:power-plug-off"
        assert state.attributes.get("active_peers_count") == 0

    async def test_power_saving_states_and_attributes(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет комбинации флагов энергосбережения и их атрибуты."""
        # 1. Оба выключены
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)

        state = next(
            s for s in hass.states.async_all(BINARY_SENSOR_DOMAIN) if "power_saving" in s.entity_id
        )
        assert state.state == STATE_OFF
        assert state.attributes.get("device_class") == BinarySensorDeviceClass.POWER
        assert state.attributes.get("icon") == "mdi:power-sleep-off"
        assert state.attributes.get("lan_power_saving") is False
        assert state.attributes.get("traffic_power_saving") is False

        coordinator = entry.runtime_data

        # 2. LAN включен
        dev_lan = dataclasses.replace(coordinator.data.device_info, lan_power_saving=True)
        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, device_info=dev_lan)
        )
        await hass.async_block_till_done()

        state = next(
            s for s in hass.states.async_all(BINARY_SENSOR_DOMAIN) if "power_saving" in s.entity_id
        )
        assert state.state == STATE_ON
        assert state.attributes.get("icon") == "mdi:power-sleep"
        assert state.attributes.get("lan_power_saving") is True
        assert state.attributes.get("traffic_power_saving") is False

        # 3. Traffic (MSPS) включен
        dev_msps = dataclasses.replace(
            coordinator.data.device_info, lan_power_saving=False, traffic_power_saving=True
        )
        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, device_info=dev_msps)
        )
        await hass.async_block_till_done()

        state = next(
            s for s in hass.states.async_all(BINARY_SENSOR_DOMAIN) if "power_saving" in s.entity_id
        )
        assert state.state == STATE_ON
        assert state.attributes.get("lan_power_saving") is False
        assert state.attributes.get("traffic_power_saving") is True

        # 4. Оба включены
        dev_both = dataclasses.replace(
            coordinator.data.device_info, lan_power_saving=True, traffic_power_saving=True
        )
        coordinator.async_set_updated_data(
            dataclasses.replace(coordinator.data, device_info=dev_both)
        )
        await hass.async_block_till_done()

        state = next(
            s for s in hass.states.async_all(BINARY_SENSOR_DOMAIN) if "power_saving" in s.entity_id
        )
        assert state.state == STATE_ON
        assert state.attributes.get("lan_power_saving") is True
        assert state.attributes.get("traffic_power_saving") is True
