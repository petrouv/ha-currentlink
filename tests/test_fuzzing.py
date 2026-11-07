"""Эмпирический фаззинг и стресс-тестирование парсера протокола TP-Link PG2400P.

Проверяет устойчивость к некорректным данным, граничным условиям,
иерархию исключений и отсутствие утечек необработанных ошибок.
"""

from __future__ import annotations

import random
import string
import time
from typing import Any

import pytest

from custom_components.currentlink.client import PG2400PClient
from custom_components.currentlink.errors import (
    PG2400PError,
    ResponseFormatError,
)
from custom_components.currentlink.models import (
    compute_telemetry_interval,
    decode_telemetry_snapshot,
)
from custom_components.currentlink.protocol import (
    ERROR_SUCCESS,
    parse_response,
)


class _MockSession:
    """Мок сессии aiohttp для проверки клиентских методов."""

    closed = False

    def __init__(self, text: str = "", status: int = 200) -> None:
        self.text = text
        self.status = status

    def get(self, *args: Any, **kwargs: Any) -> Any:
        class _Resp:
            status = self.status

            async def text(r: Any) -> str:
                return self.text

            async def __aenter__(r: Any) -> _Resp:
                return r

            async def __aexit__(r: Any, *a: Any) -> None:
                pass

        return _Resp()


# ============================================================================
# 1. Фаззинг parse_response: окончания строк
# ============================================================================


class TestParseResponseLineEndings:
    """Проверка обработки различных и смешанных окончаний строк."""

    @pytest.mark.parametrize(
        "delim_name,delim",
        [
            ("crlf", "\r\n"),
            ("lf", "\n"),
            ("cr", "\r"),
        ],
    )
    def test_uniform_line_endings(self, delim_name: str, delim: str) -> None:
        """Парсинг ответа с однородными окончаниями строк (CRLF, LF, CR)."""
        payload = f"ERROR=000{delim}KEY1=VAL1{delim}KEY2=VAL2{delim}"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data == {"KEY1": "VAL1", "KEY2": "VAL2"}

    def test_mixed_line_endings_in_single_payload(self) -> None:
        """Смешанные окончания строк (\r\n, \n, \r) в одном теле ответа."""
        payload = "ERROR=000\r\nKEY1=VAL1\nKEY2=VAL2\rKEY3=VAL3\r\r\n\nKEY4=VAL4\n\r"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data == {
            "KEY1": "VAL1",
            "KEY2": "VAL2",
            "KEY3": "VAL3",
            "KEY4": "VAL4",
        }

    def test_randomized_mixed_endings(self) -> None:
        """Случайные комбинации окончаний строк для 50 строк данных."""
        delims = ["\r\n", "\n", "\r"]
        lines = ["ERROR=000"]
        expected = {}
        for i in range(50):
            lines.append(f"K_{i}=V_{i}")
            expected[f"K_{i}"] = f"V_{i}"

        payload = "".join(f"{line}{random.choice(delims)}" for line in lines)
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data == expected


# ============================================================================
# 2. Фаззинг parse_response: отсутствие ERROR= и некорректная структура
# ============================================================================


class TestParseResponseMissingError:
    """Проверка генерации ResponseFormatError при отсутствии ERROR=."""

    @pytest.mark.parametrize(
        "payload",
        [
            "",
            "   ",
            "\t\t",
            "\r\n\r\n\r\n",
            "\n\n\n",
            "\r\r\r",
            " \r\n \n \r ",
        ],
    )
    def test_empty_and_whitespace_only(self, payload: str) -> None:
        """Пустые строки и чистые пробелы вызывают ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="Empty response"):
            parse_response(payload)

    @pytest.mark.parametrize(
        "payload",
        [
            "KEY1=VAL1\r\nKEY2=VAL2\r\n",
            "PRODUCT=PG2400P\r\nERROR=000\r\n",
            "<html><head><title>500</title></head><body>Error</body></html>",
            "Traceback (most recent call last):\n  File 'app.py', line 1\n",
            "OK\r\nKEY=VAL\r\n",
            "STATUS=000\r\nKEY=VAL\r\n",
            "error=000\r\nKEY=VAL\r\n",  # нижний регистр
        ],
    )
    def test_missing_error_header(self, payload: str) -> None:
        """Отсутствие заголовка ERROR= на первой непустой строке вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError) as exc_info:
            parse_response(payload)
        assert issubclass(exc_info.type, PG2400PError)


# ============================================================================
# 3. Фаззинг parse_response: статусные коды (валидные и невалидные)
# ============================================================================


class TestParseResponseStatusCodes:
    """Проверка поведения при различных статусных кодах."""

    @pytest.mark.parametrize(
        "code_str",
        ["000", "004", "006", "009", "123", "999", "XYZ", "-1", "ERR_UNKNOWN"],
    )
    def test_status_codes_parsed_as_string(self, code_str: str) -> None:
        """Парсер извлекает любой непустой строковый код статуса."""
        payload = f"ERROR={code_str}\r\nKEY=VAL\r\n"
        code, data = parse_response(payload)
        assert code == code_str
        assert data == {"KEY": "VAL"}

    @pytest.mark.parametrize(
        "bad_error_line",
        [
            "ERROR=\r\n",
            "ERROR=   \r\n",
            "ERROR=\t\r\n",
            "ERROR\r\n",
            "ERROR \r\n",
        ],
    )
    def test_empty_or_missing_status_code_raises(self, bad_error_line: str) -> None:
        """Пустой код ошибки или строка без '=' вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError) as exc_info:
            parse_response(bad_error_line)
        assert issubclass(exc_info.type, PG2400PError)

    def test_status_code_with_leading_equal(self) -> None:
        """Строка ERROR==000 разбирается с кодом '=000'."""
        code, data = parse_response("ERROR==000\r\nKEY=VAL\r\n")
        assert code == "=000"
        assert data == {"KEY": "VAL"}


# ============================================================================
# 4. Фаззинг parse_response: значения со знаками '=', пробелами, Unicode и binary
# ============================================================================


class TestParseResponseValues:
    """Проверка сохранения значений после первого знака '='."""

    def test_multiple_equals_in_value(self) -> None:
        """Значения, содержащие несколько знаков '=', сохраняются полностью."""
        payload = (
            "ERROR=000\r\n"
            "TOKEN=abc=def==ghi===jkl\r\n"
            "BASE64=eyJhYmMiOiIxMjMifQ==\r\n"
            "EQUATION=a=b=c\r\n"
        )
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data["TOKEN"] == "abc=def==ghi===jkl"
        assert data["BASE64"] == "eyJhYmMiOiIxMjMifQ=="
        assert data["EQUATION"] == "a=b=c"

    def test_spaces_in_value_and_around_keys(self) -> None:
        """Пробелы вокруг ключей удаляются, внутренние пробелы значений сохраняются."""
        payload = (
            "ERROR=000\r\n"
            "  KEY1  =   value with multiple spaces inside   \r\n"
            "KEY2=value=with=equals and spaces\r\n"
        )
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data["KEY1"] == "value with multiple spaces inside"
        assert data["KEY2"] == "value=with=equals and spaces"

    def test_unicode_and_special_characters(self) -> None:
        """Символы UTF-8 (кириллица, emoji, математические знаки) сохраняются корректно."""
        payload = (
            "ERROR=000\r\n"
            "DEVICE_NAME=Адаптер Кабинет ⚡🔌\r\n"
            "NOTE=Проверка работы: ∑(x_i) >= 100% & <тест>\r\n"
            "CHINESE=普联技术有限公司\r\n"
        )
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data["DEVICE_NAME"] == "Адаптер Кабинет ⚡🔌"
        assert data["NOTE"] == "Проверка работы: ∑(x_i) >= 100% & <тест>"
        assert data["CHINESE"] == "普联技术有限公司"

    def test_binary_and_null_bytes(self) -> None:
        """Строка с нулевыми и управляющими байтами сохраняется без исключений."""
        payload = "ERROR=000\r\nRAW_DATA=abc\x00def\x01ghi\xff\r\n"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data["RAW_DATA"] == "abc\x00def\x01ghi\xff"


# ============================================================================
# 5. Фаззинг parse_response: дублирующиеся ключи
# ============================================================================


class TestParseResponseDuplicates:
    """Проверка строгой обработки дублирующихся ключей."""

    def test_duplicate_data_key_raises(self) -> None:
        """Дублирующийся ключ данных вызывает ResponseFormatError с указанием номера строки."""
        payload = "ERROR=000\r\nKEY=FIRST\r\nKEY=SECOND\r\n"
        with pytest.raises(ResponseFormatError, match="duplicate key 'KEY'") as exc_info:
            parse_response(payload)
        assert exc_info.value.line_number == 3

    def test_duplicate_key_with_whitespace_variance_raises(self) -> None:
        """Дублирующийся ключ с разным количеством окружающих пробелов обнаруживается."""
        payload = "ERROR=000\r\n  KEY  =FIRST\r\n\tKEY\t=SECOND\r\n"
        with pytest.raises(ResponseFormatError, match="duplicate key 'KEY'"):
            parse_response(payload)

    def test_duplicate_error_in_body_raises(self) -> None:
        """Повторный заголовок ERROR дважды в теле ответа вызывает ResponseFormatError."""
        payload = "ERROR=000\r\nERROR=004\r\nERROR=006\r\n"
        with pytest.raises(ResponseFormatError, match="duplicate key 'ERROR'"):
            parse_response(payload)


# ============================================================================
# 6. Стресс-тестирование масштабируемости (100KB - 1.5MB)
# ============================================================================


class TestParseResponsePayloadScale:
    """Стресс-тесты больших объёмов данных и линейности времени выполнения."""

    def test_huge_payload_100kb(self) -> None:
        """Парсинг нагрузки размером свыше 100KB (2 500 ключей)."""
        lines = ["ERROR=000"]
        for i in range(2500):
            lines.append(f"KEY_{i:04d}=VALUE_{i:04d}_PADDING_DATA_FOR_PAYLOAD_SIZE")
        payload = "\r\n".join(lines) + "\r\n"
        assert len(payload.encode("utf-8")) > 100 * 1024

        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert len(data) == 2500
        assert data["KEY_2499"].startswith("VALUE_2499")

    def test_massive_payload_1mb_linear_time(self) -> None:
        """Парсинг 1.35 МБ полезной нагрузки (20 000 строк) выполняется быстрее 100 мс."""
        lines = ["ERROR=000"]
        for i in range(20000):
            lines.append(f"MASSIVE_K_{i:05d}=MASSIVE_V_{i:05d}_PAYLOAD_PADDING_HEX_DATA")
        payload = "\r\n".join(lines) + "\r\n"
        assert len(payload.encode("utf-8")) > 1024 * 1024

        t0 = time.perf_counter()
        code, data = parse_response(payload)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert code == ERROR_SUCCESS
        assert len(data) == 20000
        assert elapsed_ms < 200.0  # Гарантия O(N), отсутствие квадратичного замедления

    def test_huge_single_value_100kb(self) -> None:
        """Парсинг одиночного поля размером 100KB (например, дампа конфигурации)."""
        giant_value = "A" * (100 * 1024)
        payload = f"ERROR=000\r\nGIANT_BUFFER={giant_value}\r\n"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data["GIANT_BUFFER"] == giant_value


# ============================================================================
# 7. Фаззинг decode_telemetry_snapshot
# ============================================================================


class TestDecodeTelemetrySnapshotFuzzing:
    """Фаззинг декодера 20 полей скрытой телеметрии G.hn Wave-2."""

    def test_empty_dict_fallback(self) -> None:
        """Передача пустого словаря {} не вызывает KeyError и выставляет безопасные defaults."""
        snap = decode_telemetry_snapshot({}, timestamp=12345.0)
        assert snap.timestamp == 12345.0
        assert snap.uptime_seconds == 12345.0
        assert snap.attenuation_db is None
        assert snap.wire_length_m is None
        assert snap.xput_indicator_mbps == 0
        assert snap.g9962_counters == {}
        assert snap.channel_adaptations == {}
        assert snap.eth_stats == {}
        assert snap.master_losses == 0
        assert snap.qos_discarded_pkts == 0
        assert snap.raw_fields == {}

    @pytest.mark.parametrize(
        "bad_val",
        [
            "N/A",
            "error",
            "none",
            "null",
            "NaN",
            "inf",
            "-inf",
            "0x10",
            "12.34",
            "UNKNOWN",
            "???",
            "",
            "   ",
        ],
    )
    def test_invalid_integer_strings_in_rate_and_counters(self, bad_val: str) -> None:
        """Нечисловые строки в числовых полях безопасно игнорируются без ValueError."""
        fields = {
            "FLOWMONITOR.INFO.XPUT_INDICATOR": bad_val,
            "QOS.STATS.INFO": f"1000,{bad_val},2000",
            "DIDMNG.GENERAL.AVG_ATTENUATION": f"0,0,{bad_val}",
            "DIDMNG.GENERAL.WIRE_LENGTH": f"0,0,{bad_val}",
            "QOS.STATS.G9962_DESC": "Bytes_TX, Bytes_RX, BLOCKS_TX",
            "QOS.STATS.G9962": f"100,{bad_val},300",
            "MASTERSELECTION.STATS.INFO": f"1,2,{bad_val}",
        }
        snap = decode_telemetry_snapshot(fields, timestamp=100.0)
        assert snap.xput_indicator_mbps == 0
        assert snap.attenuation_db is None
        assert snap.wire_length_m is None
        # Подавленный bad_val в G9962
        assert "Bytes_TX" in snap.g9962_counters
        assert snap.g9962_counters["Bytes_TX"] == 100

    def test_extreme_integer_length_limit_handled(self) -> None:
        """Число из 5000 цифр (>4300 limit в Python 3.11+) не ломает парсер."""
        huge_int_str = "9" * 5000
        fields = {
            "FLOWMONITOR.INFO.XPUT_INDICATOR": huge_int_str,
            "QOS.STATS.G9962_DESC": "A,B",
            "QOS.STATS.G9962": f"100,{huge_int_str}",
        }
        snap = decode_telemetry_snapshot(fields)
        # ValueError(Exceeds the limit 4300 digits) подавляется
        assert snap.xput_indicator_mbps == 0
        assert snap.g9962_counters.get("A") == 100

    def test_mismatched_series_and_descriptions(self) -> None:
        """Несовпадение количества заголовков и значений в CSV обрабатывается корректно."""
        # Больше значений, чем заголовков -> суффикс UNMAPPED_X
        fields_more_vals = {
            "QOS.STATS.G9962_DESC": "Bytes_TX",
            "QOS.STATS.G9962": "1000, 2000, 3000",
        }
        snap1 = decode_telemetry_snapshot(fields_more_vals)
        assert snap1.g9962_counters == {
            "Bytes_TX": 1000,
            "UNMAPPED_1": 2000,
            "UNMAPPED_2": 3000,
        }

        # Больше заголовков, чем значений -> лишние опускаются
        fields_more_names = {
            "QOS.STATS.G9962_DESC": "Bytes_TX, Bytes_RX, BLOCKS_TX",
            "QOS.STATS.G9962": "1000",
        }
        snap2 = decode_telemetry_snapshot(fields_more_names)
        assert snap2.g9962_counters == {"Bytes_TX": 1000}

    def test_compute_interval_with_fuzzed_snapshots(self) -> None:
        """Расчёт дельт между пустыми снимками возвращает TelemetryInterval или None."""
        snap_empty1 = decode_telemetry_snapshot({}, timestamp=100.0)
        snap_empty2 = decode_telemetry_snapshot({}, timestamp=130.0)
        interval = compute_telemetry_interval(snap_empty1, snap_empty2)
        assert interval is not None
        assert interval.duration_seconds == 30.0
        assert interval.throughput_tx_mbps == 0.0
        assert interval.retransmission_rate_pct == 0.0
        assert interval.receive_bler_pct == 0.0


# ============================================================================
# 8. Фаззинг пиров и MAC-адресов
# ============================================================================


class TestPeerDecodingAndClientFuzzing:
    """Фаззинг декодирования удалённых пиров и обработки MAC-адресов."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "peer_mac_input,expected_mac",
        [
            ("AA:BB:CC:00:11:33", "aa:bb:cc:00:11:33"),
            ("aa:bb:cc:00:11:33", "aa:bb:cc:00:11:33"),
            ("  AA:BB:CC:00:11:33  ", "aa:bb:cc:00:11:33"),
        ],
    )
    async def test_peer_mac_capitalization_handling(
        self,
        peer_mac_input: str,
        expected_mac: str,
    ) -> None:
        """Различный регистр символов в MAC-адресе нормализуется в нижний регистр."""
        payload = (
            "ERROR=000\r\n"
            "SYSTEM.PRODUCTION.MAC_ADDR=AA:BB:CC:00:11:22\r\n"
            f"DIDMNG.GENERAL.MACS=00:00:00:00:00:00,AA:BB:CC:00:11:22,{peer_mac_input}\r\n"
            "DIDMNG.GENERAL.RX_BPS=0,0,16000\r\n"
            "DIDMNG.GENERAL.TX_BPS=0,0,12800\r\n"
        )
        session = _MockSession(payload)
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-token"

        peers = await client.get_peers()
        assert len(peers) == 1
        assert peers[0].peer_mac == expected_mac
        assert peers[0].rx_rate_mbps == 500
        assert peers[0].tx_rate_mbps == 400

    @pytest.mark.asyncio
    async def test_get_peers_invalid_rate_string_raises_response_format_error(self) -> None:
        """Нечисловая скорость в массиве пиров вызывает ResponseFormatError (не ValueError)."""
        payload = (
            "ERROR=000\r\n"
            "SYSTEM.PRODUCTION.MAC_ADDR=aa:bb:cc:00:11:22\r\n"
            "DIDMNG.GENERAL.MACS=00:00:00:00:00:00,aa:bb:cc:00:11:33\r\n"
            "DIDMNG.GENERAL.RX_BPS=0,N/A\r\n"
            "DIDMNG.GENERAL.TX_BPS=0,12800\r\n"
        )
        session = _MockSession(payload)
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-token"

        with pytest.raises(ResponseFormatError, match="non-integer"):
            await client.get_peers()

    @pytest.mark.asyncio
    async def test_get_peers_omitted_key_raises_response_format_error(self) -> None:
        """Пропуск адаптером одного из ключей DIDMNG вызывает ResponseFormatError (не KeyError)."""
        payload = (
            "ERROR=000\r\n"
            "SYSTEM.PRODUCTION.MAC_ADDR=aa:bb:cc:00:11:22\r\n"
            "DIDMNG.GENERAL.MACS=00:00:00:00:00:00,aa:bb:cc:00:11:33\r\n"
            "DIDMNG.GENERAL.RX_BPS=0,16000\r\n"
            # DIDMNG.GENERAL.TX_BPS пропущен
        )
        session = _MockSession(payload)
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-token"

        with pytest.raises(ResponseFormatError, match="omitted requested key"):
            await client.get_peers()


# ============================================================================
# 9. Рандомизированный фаззинг (1 000 итераций)
# ============================================================================


class TestRandomizedFuzzHarness:
    """Массовый фаззинг случайным шумом для выявления утечек исключений."""

    def test_fuzz_parse_response_random_strings(self) -> None:
        """1000 случайных строк вызывают ResponseFormatError или разбираются чисто."""
        alphabet = string.ascii_letters + string.digits + ' \t\r\n=_-:./<>!@#$%^&*()[]{};,"\x00\xff'
        delims = ["\r\n", "\n", "\r"]
        err_codes = ["000", "004", "006", "009", "XYZ", "-1", ""]

        leaked_exceptions = []
        for _ in range(1000):
            lines = []
            if random.random() < 0.4:
                lines.append("ERROR=" + random.choice(err_codes))
            for _ in range(random.randint(0, 15)):
                line_len = random.randint(0, 40)
                lines.append("".join(random.choice(alphabet) for _ in range(line_len)))

            payload = "".join(f"{line}{random.choice(delims)}" for line in lines)

            try:
                parse_response(payload)
            except ResponseFormatError:
                pass
            except Exception as exc:
                leaked_exceptions.append((type(exc).__name__, str(exc)))

        assert not leaked_exceptions, f"Leaked unhandled exceptions: {leaked_exceptions}"

    def test_fuzz_decode_telemetry_snapshot_random_dictionaries(self) -> None:
        """1000 случайных словарей с произвольными ключами/значениями не приводят к сбоям."""
        alphabet = string.ascii_letters + string.digits + " ,.-_+~!=<>/:;"
        keys_pool = [
            "DIDMNG.GENERAL.AVG_ATTENUATION",
            "DIDMNG.GENERAL.WIRE_LENGTH",
            "FLOWMONITOR.INFO.XPUT_INDICATOR",
            "QOS.STATS.INFO",
            "QOS.STATS.G9962",
            "QOS.STATS.G9962_DESC",
            "RANDOM_UNKNOWN_KEY",
        ]

        leaked_exceptions = []
        prev = decode_telemetry_snapshot({})

        for _ in range(1000):
            fields = {}
            for k in random.sample(keys_pool, random.randint(0, len(keys_pool))):
                mode = random.choice(["int_csv", "noise", "empty", "bad_int"])
                if mode == "int_csv":
                    nums = [str(random.randint(-100, 100000)) for _ in range(random.randint(1, 5))]
                    fields[k] = ",".join(nums)
                elif mode == "bad_int":
                    fields[k] = random.choice(["N/A", "error", "none", "???"])
                elif mode == "empty":
                    fields[k] = ""
                else:
                    raw_chars = [random.choice(alphabet) for _ in range(random.randint(0, 20))]
                    fields[k] = "".join(raw_chars)

            try:
                snap = decode_telemetry_snapshot(fields)
                compute_telemetry_interval(prev, snap)
                prev = snap
            except Exception as exc:
                leaked_exceptions.append((type(exc).__name__, str(exc)))

        assert not leaked_exceptions, f"Leaked unhandled exceptions: {leaked_exceptions}"
