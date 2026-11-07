"""Тесты парсера протокола и криптографических функций TP-Link PG2400P."""

from __future__ import annotations

import pytest

from custom_components.currentlink.errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    PG2400PError,
    ResponseFormatError,
    TokenExpiredError,
)
from custom_components.currentlink.protocol import (
    ERROR_INVALID_PASSWORD,
    ERROR_SESSION_WARMUP,
    ERROR_SUCCESS,
    ERROR_TOKEN_INVALID,
    ERROR_UNSUPPORTED,
    compute_password_hash,
    parse_response,
)


class TestComputePasswordHash:
    """Тесты вычисления MD5-хэша пароля."""

    def test_known_vector_strong_password(self) -> None:
        """Проверка известного тестового вектора."""
        assert compute_password_hash("MyStrongPassword") == "f78e7ab810633ab3a6bbaa49d7d6d5eb"

    def test_empty_password(self) -> None:
        """Хэш пустой строки равен стандартному MD5 пустоты."""
        assert compute_password_hash("") == "d41d8cd98f00b204e9800998ecf8427e"

    def test_standard_admin(self) -> None:
        """Хэш пароля admin."""
        assert compute_password_hash("admin") == "21232f297a57a5a743894a0e4a801fc3"

    def test_unicode_password(self) -> None:
        """Хэш пароля с символами UTF-8 формирует 32-значный hex в нижнем регистре."""
        digest = compute_password_hash("П@роль_123$€")
        assert len(digest) == 32
        assert digest.islower()
        assert all(c in "0123456789abcdef" for c in digest)

    @pytest.mark.parametrize("invalid_input", [None, 12345, b"bytes", [], {}])
    def test_invalid_type_raises_type_error(self, invalid_input: object) -> None:
        """Передача нестрокового типа вызывает TypeError."""
        with pytest.raises(TypeError):
            compute_password_hash(invalid_input)  # type: ignore[arg-type]


class TestParseResponseValid:
    """Тесты успешного парсинга корректных ответов."""

    def test_success_single_line_crlf(self) -> None:
        """Ответ из одной строки ERROR=000 с CRLF."""
        code, data = parse_response("ERROR=000\r\n")
        assert code == ERROR_SUCCESS
        assert data == {}

    def test_success_single_line_lf(self) -> None:
        """Ответ из одной строки ERROR=000 с LF."""
        code, data = parse_response("ERROR=000\n")
        assert code == ERROR_SUCCESS
        assert data == {}

    def test_warmup_code(self) -> None:
        """Ответ с кодом ERROR=004 (ETOKEN / Session Warmup)."""
        code, data = parse_response("ERROR=004\r\n")
        assert code == ERROR_TOKEN_INVALID
        assert code == ERROR_SESSION_WARMUP
        assert data == {}

    def test_unsupported_code(self) -> None:
        """Ответ с кодом ERROR=009 (неподдерживаемый запрос)."""
        code, data = parse_response("ERROR=009\r\n")
        assert code == ERROR_UNSUPPORTED
        assert data == {}

    def test_invalid_password_with_login_times(self) -> None:
        """Ответ с кодом ERROR=006 и счётчиком неудачных попыток."""
        code, data = parse_response("ERROR=006\r\nLOGIN_TIMES=2\r\n")
        assert code == ERROR_INVALID_PASSWORD
        assert data == {"LOGIN_TIMES": "2"}

    def test_token_with_multiple_equals_preserved(self) -> None:
        """Сохранение знаков '=' внутри значения поля (например, токена)."""
        code, data = parse_response("ERROR=000\r\nTOKEN=abc=def==\r\n")
        assert code == ERROR_SUCCESS
        assert data == {"TOKEN": "abc=def=="}

    def test_multi_line_device_info(self) -> None:
        """Парсинг многострочного ответа с данными устройства."""
        payload = (
            "ERROR=000\r\n"
            "PRODUCT=PG2400P\r\n"
            "REVISION=1.0\r\n"
            "FW_VERSION=1.0.3 Build 20221213\r\n"
            "LANGUAGE=EN\r\n"
        )
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert len(data) == 4
        assert data["PRODUCT"] == "PG2400P"
        assert data["REVISION"] == "1.0"
        assert data["FW_VERSION"] == "1.0.3 Build 20221213"
        assert data["LANGUAGE"] == "EN"

    def test_empty_value_is_allowed(self) -> None:
        """Ключ с пустым значением допустим и сохраняется как пустая строка."""
        code, data = parse_response("ERROR=000\r\nEMPTY_FIELD=\r\n")
        assert code == ERROR_SUCCESS
        assert data == {"EMPTY_FIELD": ""}

    def test_whitespace_stripping(self) -> None:
        """Пробелы вокруг ключей и значений удаляются."""
        code, data = parse_response("  ERROR  =  000  \r\n  KEY  =  value  \r\n")
        assert code == ERROR_SUCCESS
        assert data == {"KEY": "value"}

    def test_interspersed_blank_lines(self) -> None:
        """Пустые строки в начале, середине и конце ответа игнорируются."""
        payload = "\r\n\r\nERROR=000\r\n\r\nKEY1=VAL1\r\n\r\nKEY2=VAL2\r\n\r\n"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert data == {"KEY1": "VAL1", "KEY2": "VAL2"}

    def test_telemetry_fixture_parsing(self, sample_telemetry_1_text: str) -> None:
        """Парсинг реального фикстурного снимка из 20 ключей телеметрии."""
        payload = f"ERROR=000\r\n{sample_telemetry_1_text}"
        code, data = parse_response(payload)
        assert code == ERROR_SUCCESS
        assert len(data) == 20
        assert data["FLOWMONITOR.INFO.XPUT_INDICATOR"] == "365"
        assert "Bytes_TX" in data["QOS.STATS.G9962_DESC"]


class TestParseResponseErrors:
    """Тесты обработки некорректных ответов."""

    @pytest.mark.parametrize("empty_payload", ["", "   ", "\r\n\r\n", "\t\n"])
    def test_empty_body_raises(self, empty_payload: str) -> None:
        """Пустой ответ вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="Empty response"):
            parse_response(empty_payload)

    def test_missing_error_header(self) -> None:
        """Отсутствие заголовка ERROR на первой строке вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="Response missing ERROR"):
            parse_response("TOKEN=abc123xyz\r\n")

    def test_error_not_on_first_line(self) -> None:
        """Заголовок ERROR не на первой строке вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="Response missing ERROR"):
            parse_response("KEY=val\r\nERROR=000\r\n")

    def test_malformed_error_line_no_equals(self) -> None:
        """Строка ERROR без знака равенства вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="no key/value separator"):
            parse_response("ERROR 000\r\n")

    def test_empty_error_code(self) -> None:
        """Пустой код ошибки вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="empty error code"):
            parse_response("ERROR=\r\n")

    def test_payload_line_no_equals(self) -> None:
        """Строка тела без знака равенства вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="no key/value separator"):
            parse_response("ERROR=000\r\nINVALID_LINE\r\n")

    def test_payload_line_empty_key(self) -> None:
        """Строка тела с пустым ключом вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="empty key"):
            parse_response("ERROR=000\r\n=value\r\n")

    def test_duplicate_key_raises(self) -> None:
        """Дублирующийся ключ вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError, match="duplicate key"):
            parse_response("ERROR=000\r\nKEY=1\r\nKEY=2\r\n")

    def test_html_error_page_raises(self) -> None:
        """HTML-страница ошибки прокси/сервера вызывает ResponseFormatError."""
        with pytest.raises(ResponseFormatError):
            parse_response("<html><body>502 Bad Gateway</body></html>")

    @pytest.mark.parametrize("invalid_input", [None, 123, b"raw", {}])
    def test_invalid_type_raises_type_error(self, invalid_input: object) -> None:
        """Передача нестрокового типа вызывает TypeError."""
        with pytest.raises(TypeError):
            parse_response(invalid_input)  # type: ignore[arg-type]


class TestExceptionHierarchy:
    """Тесты иерархии исключений."""

    def test_inheritance(self) -> None:
        """Проверка правильности наследования классов исключений."""
        assert issubclass(AuthenticationError, PG2400PError)
        assert issubclass(TokenExpiredError, PG2400PError)
        assert issubclass(ResponseFormatError, PG2400PError)
        assert issubclass(PG2400PConnectionError, PG2400PError)
        assert issubclass(ConnectionTimeoutError, PG2400PConnectionError)
        assert issubclass(ConnectionTimeoutError, PG2400PError)

    def test_attributes(self) -> None:
        """Проверка атрибутов ошибок."""
        auth_err = AuthenticationError("Auth failed", login_attempts=3)
        assert auth_err.login_attempts == 3
        assert str(auth_err) == "Auth failed"

        fmt_err = ResponseFormatError("Bad line", line_number=4, raw_line="XYZ")
        assert fmt_err.line_number == 4
        assert fmt_err.raw_line == "XYZ"
        assert str(fmt_err) == "Bad line"
