"""Тесты моделей данных, декодирования телеметрии и расчёта интервалов."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from custom_components.currentlink.models import (
    DeviceInfo,
    PeerLink,
    PG2400PData,
    PortSpeedCode,
    PortStatus,
    TelemetrySnapshot,
    compute_counter_delta,
    compute_telemetry_interval,
    decode_telemetry_snapshot,
)


class TestPortStatus:
    """Тесты модели статуса портов Ethernet."""

    def test_port_status_mapping(self) -> None:
        """Проверка маппинга стандартных кодов скорости портов."""
        status = PortStatus.from_codes(0, 6)
        assert status.port1_code == 0
        assert status.port1_speed == "Disconnected"
        assert status.port2_code == 6
        assert status.port2_speed == "1000M Full"

    def test_port_status_unknown_code(self) -> None:
        """Неизвестный код скорости маппится в строку Unknown."""
        status = PortStatus.from_codes(99, 4)
        assert status.port1_speed == "Unknown (99)"
        assert status.port2_speed == "100M Full"

    def test_port_speed_enum(self) -> None:
        """Проверка значений перечисления PortSpeedCode."""
        assert PortSpeedCode.DISCONNECTED == 0
        assert PortSpeedCode.SPEED_10M_HALF == 1
        assert PortSpeedCode.SPEED_10M_FULL == 2
        assert PortSpeedCode.SPEED_100M_HALF == 3
        assert PortSpeedCode.SPEED_100M_FULL == 4
        assert PortSpeedCode.SPEED_1000M_HALF == 5
        assert PortSpeedCode.SPEED_1000M_FULL == 6


class TestDeviceInfo:
    """Тесты модели информации об устройстве."""

    def test_device_info_immutability(self) -> None:
        """Модель DeviceInfo заморожена (неизменяема)."""
        info = DeviceInfo(
            model="PG2400P",
            hw_revision="1.0",
            fw_version="1.0.3",
            mac_address="aa:bb:cc:00:11:22",
            device_name="plc-main",
            domain_name="HomeGrid",
            ip_address="192.0.2.10",
        )
        assert info.model == "PG2400P"
        with pytest.raises(FrozenInstanceError):
            info.device_name = "new_name"  # type: ignore[misc]


class TestPeerLink:
    """Тесты модели канала до пира."""

    def test_peer_link_properties(self) -> None:
        """Проверка полей и вычисляемых свойств PeerLink."""
        peer = PeerLink(
            peer_mac="aa:bb:cc:00:11:33",
            rx_rate_mbps=500,
            tx_rate_mbps=400,
            rx_raw=16000,
            tx_raw=12800,
            active=True,
        )
        assert peer.peer_mac == "aa:bb:cc:00:11:33"
        assert peer.rx_rate_mbps == 500
        assert peer.tx_rate_mbps == 400
        assert peer.rx_raw_bps == 16000
        assert peer.tx_raw_bps == 12800
        assert peer.active is True


class TestTelemetryDecoding:
    """Тесты декодирования 20 полей скрытой телеметрии G.hn Wave-2."""

    def test_telemetry_decoding_sample_1(
        self,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Проверка распаковки значений из реального снимка телеметрии."""
        snap = sample_snapshot_1
        assert snap.xput_indicator_mbps == 365
        assert snap.attenuation_db == 43.0
        assert snap.wire_length_m == 60
        assert snap.uptime_seconds == 3385.620
        assert snap.qos_discarded_pkts == 183
        assert snap.master_losses == 1
        assert snap.g9962_counters["Bytes_TX"] == 3816290040
        assert snap.g9962_counters["Bytes_RX"] == 3475136520
        assert snap.g9962_counters["BLOCKS_TX"] == 8447504
        assert snap.g9962_counters["BLOCKS_RX"] == 7337909
        assert snap.g9962_counters["BLOCKS_RTX"] == 1795573
        assert snap.g9962_counters["BLOCKS_ERROR_RX"] == 644888
        assert snap.channel_adaptations["First Time"] == 6
        assert snap.channel_adaptations["BLER Incr"] == 21
        assert snap.channel_adaptations["UNMAPPED_1"] == 0

    def test_telemetry_special_values_uncalculated(self) -> None:
        """Затухание >= 2000 или <= 0 и длина 0 декодируются в None."""
        fields = {
            "DIDMNG.GENERAL.AVG_ATTENUATION": "0,0,2000",
            "DIDMNG.GENERAL.WIRE_LENGTH": "0,0,0",
            "FLOWMONITOR.INFO.XPUT_INDICATOR": "100",
        }
        snap = decode_telemetry_snapshot(fields)
        assert snap.attenuation_db is None
        assert snap.wire_length_m is None
        assert snap.xput_indicator_mbps == 100

    def test_telemetry_zero_attenuation_is_none(self) -> None:
        """Затухание 0 декодируется в None."""
        fields = {
            "DIDMNG.GENERAL.AVG_ATTENUATION": "0,0,0",
            "DIDMNG.GENERAL.WIRE_LENGTH": "0,0,15",
        }
        snap = decode_telemetry_snapshot(fields)
        assert snap.attenuation_db is None
        assert snap.wire_length_m == 15


class TestTelemetryIntervalCalculation:
    """Тесты математики дельт телеметрии и расчёта интервальных показателей."""

    def test_telemetry_interval_exact_match(
        self,
        sample_snapshot_1: TelemetrySnapshot,
        sample_snapshot_2: TelemetrySnapshot,
    ) -> None:
        """Строгое совпадение расчёта дельт и скоростей с эталоном реверс-инжиниринга."""
        interval = compute_telemetry_interval(sample_snapshot_1, sample_snapshot_2)
        assert interval is not None

        # Длительность: 3475.580 - 3385.620 = 89.96 с
        assert interval.duration_seconds == 89.96

        # Байты и счётчики G.9962
        assert interval.tx_bytes == 247680  # 3816537720 - 3816290040
        assert interval.rx_bytes == 108000  # 3475244520 - 3475136520
        assert interval.blocks_tx == 1697  # 8449201 - 8447504
        assert interval.blocks_rx == 698  # 7338607 - 7337909
        assert interval.blocks_rtx == 1  # 1795574 - 1795573
        assert interval.blocks_error_rx == 19  # 644907 - 644888

        # Скорости (Мбит/с): 247680 * 8 / (89.96 * 1e6) = 0.0220257...
        assert interval.throughput_tx_mbps == 0.022
        assert interval.throughput_rx_mbps == 0.0096

        # Проценты повторов и ошибок:
        # Retransmission: (1 / 1697) * 100 = 0.058927... -> 0.0589%
        assert interval.retransmission_rate_pct == 0.0589
        # Receive BLER: (19 / 698) * 100 = 2.72206... -> 2.7221%
        assert interval.receive_bler_pct == 2.7221

        # Потери Master Domain
        assert interval.master_losses_delta == 0

    def test_counter_wrap_around(self) -> None:
        """32-битное переполнение счётчика без перезагрузки рассчитывает верную дельту."""
        # 4_294_900_000 -> 100_000: разница = (100_000 + 2^32 - 4_294_900_000) = 167_296
        delta = compute_counter_delta(100_000, 4_294_900_000)
        assert delta == 167296

    def test_reboot_detection_resets_interval(
        self,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """При обнаружении уменьшения аптайма compute_telemetry_interval возвращает None."""
        rebooted_snap = TelemetrySnapshot(
            timestamp=sample_snapshot_1.timestamp + 30.0,
            uptime_seconds=12.5,  # Резкий сброс аптайма после перезагрузки
            attenuation_db=43.0,
            wire_length_m=60,
            xput_indicator_mbps=350,
            g9962_counters={"Bytes_TX": 5000, "Bytes_RX": 5000},
            channel_adaptations={},
            eth_stats={},
            master_losses=0,
        )
        assert compute_telemetry_interval(sample_snapshot_1, rebooted_snap) is None

    def test_zero_or_negative_duration_returns_none(
        self,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Интервал с нулевой или отрицательной длительностью возвращает None."""
        same_snap = sample_snapshot_1
        assert compute_telemetry_interval(same_snap, same_snap) is None


class TestPG2400PData:
    """Тесты агрегированной модели PG2400PData."""

    def test_plc_link_active_property(
        self,
        sample_snapshot_1: TelemetrySnapshot,
    ) -> None:
        """Свойство plc_link_active истинно при наличии хотя бы одного активного пира."""
        info = DeviceInfo(
            hw_revision="1.0",
            fw_version="1.0.3",
            mac_address="aa:bb:cc:00:11:22",
            device_name="plc-main",
            domain_name="HomeGrid",
            ip_address="192.0.2.10",
        )
        data_no_peers = PG2400PData(
            device_info=info,
            peers=[],
            port_status=None,
            telemetry=sample_snapshot_1,
        )
        assert data_no_peers.plc_link_active is False

        data_active_peer = PG2400PData(
            device_info=info,
            peers=[
                PeerLink(
                    peer_mac="aa:bb:cc:00:11:33",
                    rx_rate_mbps=500,
                    tx_rate_mbps=400,
                    active=True,
                )
            ],
            port_status=None,
            telemetry=sample_snapshot_1,
        )
        assert data_active_peer.plc_link_active is True
