"""Тесты платформы sensor для интеграции TP-Link PG2400P."""

from __future__ import annotations

import dataclasses
from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfLength,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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
    port1: int = 6,
    port2: int = 0,
    led_enabled: bool = True,
) -> AsyncMock:
    """Создает мок клиента PG2400PClient."""
    client = AsyncMock(spec=PG2400PClient)
    client.host = TEST_HOST
    client.get_device_info.return_value = device_info
    client.get_peers.return_value = [MOCK_PEER] if peers is None else peers
    if telemetry is not None:
        client.get_telemetry.return_value = telemetry
    client.post.return_value = {"PORT1": str(port1), "PORT2": str(port2)}
    client.query.return_value = {LED_ENABLE_KEY: "YES" if led_enabled else "NO"}
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


def _get_sensor_state(
    hass: HomeAssistant,
    registry: er.EntityRegistry,
    key: str,
    peer_mac: str | None = None,
) -> State:
    """Получает состояние сенсора по его ключу или ключу пира."""
    local_mac = TEST_MAC.lower()
    uid = f"{local_mac}_{peer_mac.lower()}_{key}" if peer_mac else f"{local_mac}_{key}"
    entity_id = registry.async_get_entity_id(SENSOR_DOMAIN, DOMAIN, uid)
    assert entity_id is not None, f"Entity not found in registry for unique_id {uid}"
    state = hass.states.get(entity_id)
    assert state is not None, f"State not found in StateMachine for entity_id {entity_id}"
    return state


class TestSensorPlatform:
    """Тестирование сенсоров TP-Link PG2400P."""

    async def test_sensor_entities_created_on_setup(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет создание всех 23 сущностей сенсоров."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)

        registry = er.async_get(hass)
        entries = er.async_entries_for_config_entry(registry, entry.entry_id)
        sensor_entries = [e for e in entries if e.domain == SENSOR_DOMAIN]

        # Должно быть ровно 26 сенсоров (24 статических + 2 для пира)
        assert len(sensor_entries) == 26

        translation_keys = {e.translation_key for e in sensor_entries if e.translation_key}
        expected_keys = {
            "port1_speed",
            "port2_speed",
            "uptime",
            "attenuation",
            "wire_length",
            "throughput",
            "retransmission_rate",
            "receive_bler",
            "throughput_tx",
            "throughput_rx",
            "bytes_tx",
            "bytes_rx",
            "pkts_tx",
            "pkts_rx",
            "blocks_tx",
            "blocks_rx",
            "blocks_rtx",
            "blocks_error_rx",
            "channel_adaptations",
            "master_losses",
            "active_peers",
            "tx_rate",
            "rx_rate",
            "phy_mode",
            "compatibility_mode",
            "qos_mode",
        }
        assert expected_keys == translation_keys

        # Проверка Device Registry
        device_reg = dr.async_get(hass)
        device = device_reg.async_get_device_by_identifier(
            (DOMAIN, TEST_MAC), config_entry_id=entry.entry_id
        )
        assert device is not None
        assert device.manufacturer == MANUFACTURER
        assert device.model == MODEL

    async def test_sensor_state_values_and_attributes(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет значения состояний, единицы измерений и классы сенсоров."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        # 1. Порты Ethernet
        s_port1 = _get_sensor_state(hass, registry, "port1_speed")
        assert s_port1.state == "1000M Full"
        assert s_port1.attributes.get("device_class") == SensorDeviceClass.ENUM
        assert s_port1.attributes.get("port_code") == 6

        s_port2 = _get_sensor_state(hass, registry, "port2_speed")
        assert s_port2.state == "Disconnected"
        assert s_port2.attributes.get("port_code") == 0

        # 2. Аптайм
        s_uptime = _get_sensor_state(hass, registry, "uptime")
        assert s_uptime.attributes.get("device_class") == SensorDeviceClass.TIMESTAMP
        assert s_uptime.attributes.get("uptime_seconds") == 3385.6
        parsed_dt = datetime.fromisoformat(s_uptime.state)
        assert parsed_dt.tzinfo is not None

        # 3. Физическая линия и PHY
        s_atten = _get_sensor_state(hass, registry, "attenuation")
        assert float(s_atten.state) == 43.0
        assert s_atten.attributes.get("unit_of_measurement") == SIGNAL_STRENGTH_DECIBELS
        assert s_atten.attributes.get("state_class") == SensorStateClass.MEASUREMENT
        assert s_atten.attributes.get("device_class") == SensorDeviceClass.SIGNAL_STRENGTH

        s_wire = _get_sensor_state(hass, registry, "wire_length")
        assert int(s_wire.state) == 60
        assert s_wire.attributes.get("unit_of_measurement") == UnitOfLength.METERS
        assert s_wire.attributes.get("device_class") == SensorDeviceClass.DISTANCE
        assert s_wire.attributes.get("state_class") == SensorStateClass.MEASUREMENT

        s_thru = _get_sensor_state(hass, registry, "throughput")
        assert int(s_thru.state) == 365
        assert s_thru.attributes.get("unit_of_measurement") == UnitOfDataRate.MEGABITS_PER_SECOND
        assert s_thru.attributes.get("device_class") == SensorDeviceClass.DATA_RATE
        assert s_thru.attributes.get("state_class") == SensorStateClass.MEASUREMENT

        # 4. Накопительные счётчики G.9962
        s_bytes_tx = _get_sensor_state(hass, registry, "bytes_tx")
        assert int(s_bytes_tx.state) == 3816290040
        assert s_bytes_tx.attributes.get("unit_of_measurement") == UnitOfInformation.BYTES
        assert s_bytes_tx.attributes.get("state_class") == SensorStateClass.TOTAL_INCREASING
        assert s_bytes_tx.attributes.get("device_class") == SensorDeviceClass.DATA_SIZE

        s_bytes_rx = _get_sensor_state(hass, registry, "bytes_rx")
        assert int(s_bytes_rx.state) == 3475136520
        assert s_bytes_rx.attributes.get("state_class") == SensorStateClass.TOTAL_INCREASING

        s_pkts_tx = _get_sensor_state(hass, registry, "pkts_tx")
        assert int(s_pkts_tx.state) == 3056736
        assert s_pkts_tx.attributes.get("unit_of_measurement") == "pkts"

        s_blocks_tx = _get_sensor_state(hass, registry, "blocks_tx")
        assert int(s_blocks_tx.state) == 8447504
        assert s_blocks_tx.attributes.get("unit_of_measurement") == "blocks"

        s_blocks_rx = _get_sensor_state(hass, registry, "blocks_rx")
        assert int(s_blocks_rx.state) == 7337909

        s_blocks_rtx = _get_sensor_state(hass, registry, "blocks_rtx")
        assert int(s_blocks_rtx.state) == 1795573

        s_blocks_err = _get_sensor_state(hass, registry, "blocks_error_rx")
        assert int(s_blocks_err.state) == 644888

        # 5. Адаптации канала и Master Domain
        s_adapt = _get_sensor_state(hass, registry, "channel_adaptations")
        assert int(s_adapt.state) == sum(sample_snapshot_1.channel_adaptations.values())
        assert s_adapt.attributes.get("Adapt End OK") == 36
        assert s_adapt.attributes.get("BLER Incr") == 21

        s_master = _get_sensor_state(hass, registry, "master_losses")
        assert int(s_master.state) == 1
        assert s_master.attributes.get("unit_of_measurement") == "losses"

        # 6. Топология
        s_peers = _get_sensor_state(hass, registry, "active_peers")
        assert int(s_peers.state) == 1
        assert s_peers.attributes.get("peer_macs") == [PEER_MAC]

        # 7. Сенсоры пира
        s_tx_rate = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_MAC)
        assert int(s_tx_rate.state) == 1180
        assert s_tx_rate.attributes.get("unit_of_measurement") == UnitOfDataRate.MEGABITS_PER_SECOND
        assert s_tx_rate.attributes.get("raw_bps") == 37768
        assert s_tx_rate.attributes.get("peer_mac") == PEER_MAC

        s_rx_rate = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_MAC)
        assert int(s_rx_rate.state) == 1254
        assert s_rx_rate.attributes.get("raw_bps") == 40144

    async def test_sensor_telemetry_interval_metrics(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет переход интервальных метрик из None в вычисленные проценты."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)

        # На первом цикле дельты ещё не вычислены
        s_rtx = _get_sensor_state(hass, registry, "retransmission_rate")
        assert s_rtx.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown")

        s_bler = _get_sensor_state(hass, registry, "receive_bler")
        assert s_bler.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown")

        s_tx_thru = _get_sensor_state(hass, registry, "throughput_tx")
        assert s_tx_thru.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown")

        # Второй цикл опроса (с дельтами между снимками 1 и 2)
        client.get_telemetry.return_value = sample_snapshot_2
        coordinator = entry.runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_rtx2 = _get_sensor_state(hass, registry, "retransmission_rate")
        assert s_rtx2.state != STATE_UNKNOWN
        assert float(s_rtx2.state) >= 0.0
        assert s_rtx2.attributes.get("unit_of_measurement") == PERCENTAGE

        s_bler2 = _get_sensor_state(hass, registry, "receive_bler")
        assert s_bler2.state != STATE_UNKNOWN
        assert float(s_bler2.state) >= 0.0
        assert s_bler2.attributes.get("unit_of_measurement") == PERCENTAGE

        s_tx_thru2 = _get_sensor_state(hass, registry, "throughput_tx")
        assert s_tx_thru2.state != STATE_UNKNOWN
        assert float(s_tx_thru2.state) >= 0.0
        assert (
            s_tx_thru2.attributes.get("unit_of_measurement") == UnitOfDataRate.MEGABITS_PER_SECOND
        )

    async def test_sensor_handling_none_values(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет корректность обработки нерассчитанных затухания и длины."""
        none_snapshot = dataclasses.replace(
            sample_snapshot_1,
            attenuation_db=None,
            wire_length_m=None,
        )
        client = _build_mock_client(telemetry=none_snapshot, port1=0, port2=0)
        client.post.return_value = {"PORT1": "0", "PORT2": "0"}
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)

        state_atten = _get_sensor_state(hass, registry, "attenuation")
        assert state_atten.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown")

        state_wire = _get_sensor_state(hass, registry, "wire_length")
        assert state_wire.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown")

        # При port_status None (сбой запроса портов)
        coordinator = entry.runtime_data
        coordinator.async_set_updated_data(dataclasses.replace(coordinator.data, port_status=None))
        await hass.async_block_till_done()

        s_port1 = _get_sensor_state(hass, registry, "port1_speed")
        assert s_port1.state == "Disconnected"

    async def test_sensor_dynamic_peers_addition(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет динамическое появление сенсоров для нового подключенного адаптера."""
        client = _build_mock_client(peers=[MOCK_PEER], telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)

        # Добавляем второго пира
        peer2 = PeerLink(
            peer_mac="00:11:22:33:44:55",
            rx_rate_mbps=850,
            tx_rate_mbps=820,
            rx_raw=27200,
            tx_raw=26240,
            active=True,
        )
        client.get_peers.return_value = [MOCK_PEER, peer2]

        coordinator = entry.runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # Проверяем появление сенсоров для peer2 по unique_id
        p2_tx = _get_sensor_state(hass, registry, "tx_rate", peer_mac="00:11:22:33:44:55")
        assert int(p2_tx.state) == 820

        p2_rx = _get_sensor_state(hass, registry, "rx_rate", peer_mac="00:11:22:33:44:55")
        assert int(p2_rx.state) == 850

    async def test_sensor_peer_entity_inactive_or_removed(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет переход сенсора пира в unavailable при отключении или active=False."""
        client = _build_mock_client(peers=[MOCK_PEER], telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)

        s_rx = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_MAC)
        assert s_rx.state == "1254"

        # Пир отключен (active=False)
        inactive_peer = dataclasses.replace(MOCK_PEER, active=False)
        client.get_peers.return_value = [inactive_peer]

        coordinator = entry.runtime_data
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_rx_inactive = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_MAC)
        assert s_rx_inactive.state == STATE_UNAVAILABLE

        # Пир снова активен
        client.get_peers.return_value = [MOCK_PEER]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_rx_active = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_MAC)
        assert s_rx_active.state == "1254"
