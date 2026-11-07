"""Эмпирические стресс-тесты сенсорной платформы TP-Link PG2400P.

Охватывает:
1. Динамическое добавление, исчезновение и повторное появление удалённых пиров.
2. Граничные значения None / нерассчитанные метрики (затухание, длина линии, ребут).
3. Экстремальные значения счётчиков (32-бит переполнение, 64-бит числа, нулевые дельты, аптайм).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.client import LED_ENABLE_KEY, PG2400PClient
from custom_components.currentlink.const import DOMAIN
from custom_components.currentlink.models import (
    DeviceInfo,
    PeerLink,
    PowerlineSettings,
    TelemetrySnapshot,
    compute_counter_delta,
    compute_telemetry_interval,
    decode_telemetry_snapshot,
)

TEST_HOST = "192.0.2.10"
TEST_PASSWORD = "secret_password"
TEST_MAC = "aa:bb:cc:00:11:22"
PEER_1_MAC = "aa:bb:cc:00:11:33"
PEER_2_MAC = "00:11:22:33:44:55"
PEER_3_MAC = "aa:bb:cc:dd:ee:ff"

MOCK_DEVICE_INFO = DeviceInfo(
    model="PG2400P",
    hw_revision="1.0",
    fw_version="1.0.3 Build 20221213 Rel.62540",
    mac_address=TEST_MAC,
    device_name="plc-main",
    domain_name="HomeNetwork",
    ip_address=TEST_HOST,
)

MOCK_PEER_1 = PeerLink(
    peer_mac=PEER_1_MAC,
    rx_rate_mbps=1254,
    tx_rate_mbps=1180,
    rx_raw=40144,
    tx_raw=37768,
    active=True,
)

MOCK_PEER_2 = PeerLink(
    peer_mac=PEER_2_MAC,
    rx_rate_mbps=850,
    tx_rate_mbps=820,
    rx_raw=27200,
    tx_raw=26240,
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
    client.get_peers.return_value = [MOCK_PEER_1] if peers is None else peers
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


class TestAdversarialDynamicPeers:
    """Стресс-тестирование динамического изменения состава удалённых пиров."""

    async def test_dynamic_peer_lifecycle_and_unavailability(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет жизненный цикл пиров: появление, дубли, отключение и восстановление."""
        client = _build_mock_client(peers=[MOCK_PEER_1], telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)
        coordinator = entry.runtime_data

        # 1. Начальное состояние: только PEER_1
        s_p1_tx = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_1_MAC)
        assert s_p1_tx.state == "1180"

        initial_entries = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == SENSOR_DOMAIN
        ]
        assert len(initial_entries) == 26

        # 2. Появление нового пира (PEER_2)
        client.get_peers.return_value = [MOCK_PEER_1, MOCK_PEER_2]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p2_tx = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_2_MAC)
        s_p2_rx = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_2_MAC)
        assert s_p2_tx.state == "820"
        assert s_p2_rx.state == "850"

        updated_entries = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == SENSOR_DOMAIN
        ]
        assert len(updated_entries) == 28  # +2 сенсора для PEER_2

        # 3. Повторный опрос с теми же двумя пирами — проверка отсутствия дублей
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        after_repeat_entries = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == SENSOR_DOMAIN
        ]
        assert len(after_repeat_entries) == 28

        # 4. Пир передается с верхним регистром MAC — не должен дублироваться
        peer_2_upper = dataclasses.replace(MOCK_PEER_2, peer_mac=PEER_2_MAC.upper())
        client.get_peers.return_value = [MOCK_PEER_1, peer_2_upper]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        after_upper_entries = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == SENSOR_DOMAIN
        ]
        assert len(after_upper_entries) == 28

        # 5. PEER_2 помечен неактивным (active=False) -> статус unavailable
        peer_2_inactive = dataclasses.replace(MOCK_PEER_2, active=False)
        client.get_peers.return_value = [MOCK_PEER_1, peer_2_inactive]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p2_tx_inactive = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_2_MAC)
        assert s_p2_tx_inactive.state == STATE_UNAVAILABLE
        # PEER_1 остаётся доступным
        s_p1_tx_still_active = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_1_MAC)
        assert s_p1_tx_still_active.state == "1180"

        # 6. PEER_2 полностью исчезает из списка peers адаптера -> статус unavailable
        client.get_peers.return_value = [MOCK_PEER_1]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p2_tx_missing = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_2_MAC)
        assert s_p2_tx_missing.state == STATE_UNAVAILABLE

        # 7. PEER_2 возвращается в сеть с новыми значениями скорости
        peer_2_recovered = dataclasses.replace(MOCK_PEER_2, tx_rate_mbps=999, rx_rate_mbps=1001)
        client.get_peers.return_value = [MOCK_PEER_1, peer_2_recovered]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p2_tx_recovered = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_2_MAC)
        s_p2_rx_recovered = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_2_MAC)
        assert s_p2_tx_recovered.state == "999"
        assert s_p2_rx_recovered.state == "1001"

        # 8. Все пиры исчезают (peers=[])
        client.get_peers.return_value = []
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p1_tx_none = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_1_MAC)
        s_p2_tx_none = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_2_MAC)
        assert s_p1_tx_none.state == STATE_UNAVAILABLE
        assert s_p2_tx_none.state == STATE_UNAVAILABLE

        # Статический сенсор active_peers корректно показывает 0 и пустой список MAC
        s_active_peers = _get_sensor_state(hass, registry, "active_peers")
        assert s_active_peers.state == "0"
        assert s_active_peers.attributes.get("peer_macs") == []

        # 9. Одновременное появление сразу двух новых пиров (PEER_2 и PEER_3)
        peer_3 = PeerLink(
            peer_mac=PEER_3_MAC,
            rx_rate_mbps=600,
            tx_rate_mbps=550,
            rx_raw=19200,
            tx_raw=17600,
            active=True,
        )
        client.get_peers.return_value = [MOCK_PEER_1, MOCK_PEER_2, peer_3]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_p3_tx = _get_sensor_state(hass, registry, "tx_rate", peer_mac=PEER_3_MAC)
        s_p3_rx = _get_sensor_state(hass, registry, "rx_rate", peer_mac=PEER_3_MAC)
        assert s_p3_tx.state == "550"
        assert s_p3_rx.state == "600"

        final_entries = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == SENSOR_DOMAIN
        ]
        assert len(final_entries) == 30  # 24 статических + 2*3 пира


class TestAdversarialNoneAndUncalculatedValues:
    """Стресс-тестирование значений None и нерассчитанных метрик."""

    @pytest.mark.parametrize(
        ("raw_atten_field", "expected_state"),
        [
            ("0,0,2000", STATE_UNKNOWN),  # Граничное пороговое значение 2000 -> None
            ("0,0,2001", STATE_UNKNOWN),  # Превышение порога -> None
            ("0,0,65535", STATE_UNKNOWN),  # Неинициализированный аппаратный код -> None
            ("0,0,0", STATE_UNKNOWN),  # Нулевой сигнал -> None
            ("0,0,-10", STATE_UNKNOWN),  # Отрицательное значение -> None
            ("", STATE_UNKNOWN),  # Пустая строка -> None
            ("0,0,1999", "199.9"),  # Максимальное допустимое значение (199.9 дБ)
            ("0,0,10", "1.0"),  # Минимальное допустимое значение (1.0 дБ)
            ("0,0,430", "43.0"),  # Типичное рабочее значение (43.0 дБ)
        ],
    )
    async def test_attenuation_boundary_values(
        self,
        hass: HomeAssistant,
        sample_telemetry_1_dict: dict[str, str],
        raw_atten_field: str,
        expected_state: str,
    ) -> None:
        """Проверяет фильтрацию сырых значений затухания без исключений."""
        fields = dict(sample_telemetry_1_dict)
        fields["DIDMNG.GENERAL.AVG_ATTENUATION"] = raw_atten_field
        snapshot = decode_telemetry_snapshot(fields, host=TEST_HOST, timestamp=1000.0)

        client = _build_mock_client(telemetry=snapshot)
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        state = _get_sensor_state(hass, registry, "attenuation")
        assert state.state == expected_state

    @pytest.mark.parametrize(
        ("raw_wire_field", "expected_state"),
        [
            ("0,0,0", STATE_UNKNOWN),  # Нулевая длина линии -> None
            ("0,0,-5", STATE_UNKNOWN),  # Отрицательная длина -> None
            ("", STATE_UNKNOWN),  # Пустая строка -> None
            ("0,0,1", "1"),  # 1 метр
            ("0,0,60", "60"),  # 60 метров
            ("0,0,300", "300"),  # 300 метров (максимум спецификации G.hn)
        ],
    )
    async def test_wire_length_boundary_values(
        self,
        hass: HomeAssistant,
        sample_telemetry_1_dict: dict[str, str],
        raw_wire_field: str,
        expected_state: str,
    ) -> None:
        """Проверяет фильтрацию сырых значений длины линии без исключений."""
        fields = dict(sample_telemetry_1_dict)
        fields["DIDMNG.GENERAL.WIRE_LENGTH"] = raw_wire_field
        snapshot = decode_telemetry_snapshot(fields, host=TEST_HOST, timestamp=1000.0)

        client = _build_mock_client(telemetry=snapshot)
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        state = _get_sensor_state(hass, registry, "wire_length")
        assert state.state == expected_state

    async def test_telemetry_interval_none_on_adapter_reboot(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Проверяет переход интервальных сенсоров в None при обнаружении ребута."""
        client = _build_mock_client(telemetry=sample_snapshot_1)
        entry = await _setup_integration(hass, client)
        registry = er.async_get(hass)
        coordinator = entry.runtime_data

        # Цикл 2: успешное вычисление дельт
        client.get_telemetry.return_value = sample_snapshot_2
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_bler = _get_sensor_state(hass, registry, "receive_bler")
        assert s_bler.state != STATE_UNKNOWN

        # Цикл 3: внезапный ребут адаптера (uptime падает до 15 секунд)
        reboot_snapshot = dataclasses.replace(
            sample_snapshot_2,
            timestamp=sample_snapshot_2.timestamp + 30.0,
            uptime_seconds=15.0,  # Резкое падение аптайма
        )
        client.get_telemetry.return_value = reboot_snapshot
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # Все интервальные сенсоры должны безопасно стать STATE_UNKNOWN (None)
        for key in ("retransmission_rate", "receive_bler", "throughput_tx", "throughput_rx"):
            st = _get_sensor_state(hass, registry, key)
            assert st.state in (STATE_UNKNOWN, STATE_UNAVAILABLE), (
                f"Sensor {key} not unknown on reboot: {st.state}"
            )

        # Цикл 4: адаптер продолжает работу (аптайм 45 секунд) -> метрики восстанавливаются
        recovered_snapshot = dataclasses.replace(
            sample_snapshot_2,
            timestamp=sample_snapshot_2.timestamp + 60.0,
            uptime_seconds=45.0,
        )
        client.get_telemetry.return_value = recovered_snapshot
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        s_bler_rec = _get_sensor_state(hass, registry, "receive_bler")
        assert s_bler_rec.state != STATE_UNKNOWN

    async def test_resilience_to_completely_empty_telemetry_fields(
        self,
        hass: HomeAssistant,
    ) -> None:
        """Проверяет, что все 21 статический сенсор не падают при пустой телеметрии."""
        empty_snapshot = TelemetrySnapshot(
            timestamp=1000.0,
            uptime_seconds=0.0,
            attenuation_db=None,
            wire_length_m=None,
            xput_indicator_mbps=0,
            g9962_counters={},
            channel_adaptations={},
            eth_stats={},
            master_losses=0,
            qos_discarded_pkts=0,
            raw_fields={},
        )
        client = _build_mock_client(telemetry=empty_snapshot, port1=0, port2=0)
        client.post.return_value = {}  # Порты не вернулись
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        # Проверка всех статических сенсоров
        static_keys = (
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
        )
        for key in static_keys:
            st = _get_sensor_state(hass, registry, key)
            assert st is not None, f"Sensor {key} failed to return a state"

        # Специфические значения при пустых данных
        assert _get_sensor_state(hass, registry, "port1_speed").state == "Disconnected"
        assert _get_sensor_state(hass, registry, "uptime").state == STATE_UNKNOWN
        assert _get_sensor_state(hass, registry, "attenuation").state == STATE_UNKNOWN
        assert _get_sensor_state(hass, registry, "wire_length").state == STATE_UNKNOWN
        assert _get_sensor_state(hass, registry, "bytes_tx").state == STATE_UNKNOWN
        assert _get_sensor_state(hass, registry, "channel_adaptations").state == "0"
        assert _get_sensor_state(hass, registry, "master_losses").state == "0"
        assert _get_sensor_state(hass, registry, "active_peers").state == "1"


class TestAdversarialExtremeCounterValues:
    """Стресс-тестирование экстремальных значений счётчиков и конверсии меток времени."""

    async def test_32bit_counter_rollover_calculation(self) -> None:
        """Проверяет переполнение 32-битного аппаратного счётчика Xtensa."""
        # Аппаратное переполнение: prev близко к 2^32 - 1, curr обнулился
        prev_val = 4_294_967_290
        curr_val = 10
        delta = compute_counter_delta(curr_val, prev_val)
        # 4294967296 - 4294967290 + 10 = 16
        assert delta == 16

    async def test_64bit_counters_and_massive_spikes(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет передачу 64-битных счётчиков без искажения и вычисление дельт при спайках."""
        huge_bytes_tx = 9_223_372_036_854_775_807  # 2^63 - 1
        huge_bytes_rx = 18_446_744_073_709_551_615  # 2^64 - 1
        huge_pkts = 5_000_000_000  # > 32-bit

        huge_counters = dict(sample_snapshot_1.g9962_counters)
        huge_counters["Bytes_TX"] = huge_bytes_tx
        huge_counters["Bytes_RX"] = huge_bytes_rx
        huge_counters["PKTS_TX"] = huge_pkts

        snapshot = dataclasses.replace(sample_snapshot_1, g9962_counters=huge_counters)
        client = _build_mock_client(telemetry=snapshot)
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        # Проверяем, что Home Assistant сохраняет полное 64-битное число в строковом состоянии
        s_tx = _get_sensor_state(hass, registry, "bytes_tx")
        assert int(s_tx.state) == huge_bytes_tx

        s_rx = _get_sensor_state(hass, registry, "bytes_rx")
        assert int(s_rx.state) == huge_bytes_rx

        s_pkts = _get_sensor_state(hass, registry, "pkts_tx")
        assert int(s_pkts.state) == huge_pkts

    async def test_zero_deltas_idle_connection(
        self,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверяет вычисление дельт при отсутствии трафика (0 дельт, ZeroDivision)."""
        snap1 = sample_snapshot_1
        snap2 = dataclasses.replace(
            sample_snapshot_1,
            timestamp=sample_snapshot_1.timestamp + 30.0,
            uptime_seconds=sample_snapshot_1.uptime_seconds + 30.0,
        )

        interval = compute_telemetry_interval(snap1, snap2)
        assert interval is not None
        assert interval.tx_bytes == 0
        assert interval.rx_bytes == 0
        assert interval.blocks_tx == 0
        assert interval.blocks_rx == 0
        assert interval.throughput_tx_mbps == 0.0
        assert interval.throughput_rx_mbps == 0.0
        assert interval.retransmission_rate_pct == 0.0
        assert interval.receive_bler_pct == 0.0
        assert interval.master_losses_delta == 0

    @pytest.mark.parametrize(
        ("uptime_seconds", "should_be_valid"),
        [
            (0.0, False),  # 0.0 -> STATE_UNKNOWN
            (-100.0, False),  # Отрицательный аптайм -> STATE_UNKNOWN
            (1.0, True),  # 1 секунда -> валидный timestamp
            (4294967.295, True),  # ~49.7 дней (макс 32-бит msec) -> валидный timestamp
            (315360000.0, True),  # 10 лет -> валидный timestamp
        ],
    )
    async def test_uptime_timestamp_conversion_edge_cases(
        self,
        hass: HomeAssistant,
        sample_snapshot_1: TelemetrySnapshot,
        uptime_seconds: float,
        should_be_valid: bool,
    ) -> None:
        """Проверяет конверсию аптайма в ISO timestamp при граничных значениях времени."""
        snapshot = dataclasses.replace(sample_snapshot_1, uptime_seconds=uptime_seconds)
        client = _build_mock_client(telemetry=snapshot)
        await _setup_integration(hass, client)
        registry = er.async_get(hass)

        st = _get_sensor_state(hass, registry, "uptime")
        if not should_be_valid:
            assert st.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        else:
            assert st.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE)
            dt = datetime.fromisoformat(st.state)
            assert dt.tzinfo is not None
