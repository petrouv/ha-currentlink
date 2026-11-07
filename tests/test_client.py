"""Изолированные юнит-тесты асинхронного клиента PG2400PClient."""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import aiohttp
import pytest

from custom_components.currentlink.client import (
    DEFAULT_TIMEOUT,
    LED_ENABLE_KEY,
    LOGIN_PASSWORD_KEY,
    REBOOT_KEY,
    TELEMETRY_KEYS,
    PG2400PClient,
)
from custom_components.currentlink.errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    ResponseFormatError,
    TokenExpiredError,
)
from custom_components.currentlink.protocol import (
    compute_password_hash,
)


class _FakeResp:
    """Имитатор ответа aiohttp.ClientResponse."""

    def __init__(self, status: int, text_body: str) -> None:
        self.status = status
        self._text = text_body

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _FakeResp:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        return False


class _FakeSession:
    """Имитатор aiohttp.ClientSession с детерминированной очередью ответов."""

    def __init__(self, scripted: list[tuple[int, str] | Exception]) -> None:
        self._scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def get(self, url: str, **kwargs: Any) -> _FakeResp:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        if not self._scripted:
            raise RuntimeError(f"Unexpected GET call to {url}, queue is empty")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return _FakeResp(status, body)

    def post(self, url: str, **kwargs: Any) -> _FakeResp:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        if not self._scripted:
            raise RuntimeError(f"Unexpected POST call to {url}, queue is empty")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        return _FakeResp(status, body)

    async def close(self) -> None:
        self.closed = True


class TestClientHostValidation:
    """Тесты валидации хоста при создании клиента."""

    @pytest.mark.parametrize(
        "invalid_host",
        [
            "",
            "   ",
            "http://192.0.2.10",
            "https://192.0.2.10",
            "192.0.2.10/path",
            "192.0.2.10:80#frag",
            "192.0.2.10?param=1",
            "192.0.2.10 with space",
        ],
    )
    def test_client_rejects_invalid_host(self, invalid_host: str) -> None:
        """Клиент отклоняет некорректный хост до выполнения запросов."""
        with pytest.raises(ValueError):
            PG2400PClient(host=invalid_host, password="secret")

    def test_client_strips_valid_host(self) -> None:
        """Корректный хост с окружающими пробелами нормализуется."""
        client = PG2400PClient(host="  192.0.2.10  ", password="secret")
        assert client.host == "192.0.2.10"
        assert client.timeout == DEFAULT_TIMEOUT


class TestClientSessionLifecycle:
    """Тесты управления жизненным циклом сессии aiohttp."""

    @pytest.mark.asyncio
    async def test_session_lifecycle_external_not_closed(self) -> None:
        """Внешняя сессия (из Home Assistant) не закрывается при client.close()."""
        session = _FakeSession([])
        client = PG2400PClient(host="192.0.2.10", password="secret", session=session)  # type: ignore[arg-type]
        await client.close()
        assert session.closed is False

    @pytest.mark.asyncio
    async def test_session_lifecycle_internal_is_closed(self) -> None:
        """Внутренняя сессия закрывается при client.close()."""
        client = PG2400PClient(host="192.0.2.10", password="secret", session=None)
        session = await client._get_session()
        assert client._owns_session is True
        await client.close()
        assert session.closed is True

    @pytest.mark.asyncio
    async def test_client_async_context_manager(self) -> None:
        """Клиент работает как асинхронный контекстный менеджер."""
        client = PG2400PClient(host="192.0.2.10", password="secret", session=None)
        async with client as c:
            session = await c._get_session()
            assert session.closed is False
        assert session.closed is True


class TestClientAuthentication:
    """Тесты аутентификации и механизма Session Warmup Retry."""

    @pytest.mark.asyncio
    async def test_login_success(self) -> None:
        """Успешный вход: Preflight GET / + Login POST / с хэшем пароля."""
        session = _FakeSession(
            [
                (200, "<html>SPA</html>"),  # Preflight
                (200, "ERROR=000\r\nTOKEN=token-abc-123\r\n"),  # Login POST
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="MyPassword", session=session)  # type: ignore[arg-type]
        token = await client.login()

        assert token == "token-abc-123"
        assert client.authenticated is True
        assert len(session.calls) == 2
        assert session.calls[0]["method"] == "GET"
        assert session.calls[1]["method"] == "POST"
        assert session.calls[1]["data"] == {LOGIN_PASSWORD_KEY: compute_password_hash("MyPassword")}

    @pytest.mark.asyncio
    async def test_login_session_warmup_retry(self) -> None:
        """При коде ERROR=004 на первом входе выполняется повторный preflight + login."""
        session = _FakeSession(
            [
                (200, "<html>SPA</html>"),  # Preflight 1
                (200, "ERROR=004\r\n"),  # Login POST 1 (Warmup)
                (200, "<html>SPA</html>"),  # Preflight 2
                (200, "ERROR=000\r\nTOKEN=warmup-token-777\r\n"),  # Login POST 2 (Success)
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="MyPassword", session=session)  # type: ignore[arg-type]
        token = await client.login()

        assert token == "warmup-token-777"
        assert client.authenticated is True
        assert len(session.calls) == 4

    @pytest.mark.asyncio
    async def test_login_invalid_password_raises_auth_error(self) -> None:
        """Неверный пароль (ERROR=006) вызывает AuthenticationError с числом попыток."""
        session = _FakeSession(
            [
                (200, "<html>SPA</html>"),
                (200, "ERROR=006\r\nLOGIN_TIMES=3\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="wrong", session=session)  # type: ignore[arg-type]
        with pytest.raises(AuthenticationError) as exc_info:
            await client.login()

        assert exc_info.value.login_attempts == 3
        assert "rejected password" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_login_missing_token_raises_format_error(self) -> None:
        """Ответ ERROR=000 без поля TOKEN вызывает ResponseFormatError."""
        session = _FakeSession(
            [
                (200, "<html>SPA</html>"),
                (200, "ERROR=000\r\nSOMETHING_ELSE=1\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        with pytest.raises(ResponseFormatError, match="missing TOKEN"):
            await client.login()


class TestClientQueryAndPost:
    """Тесты выполнения запросов query и post."""

    @pytest.mark.asyncio
    async def test_query_empty_keys_returns_empty(self) -> None:
        """Запрос без ключей возвращает пустой словарь без вызова сети."""
        session = _FakeSession([])
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        result = await client.query([])
        assert result == {}
        assert len(session.calls) == 0

    @pytest.mark.asyncio
    async def test_query_success_with_active_token(self) -> None:
        """Выполнение query с уже сохранённым токеном."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\nKEY1=VAL1\r\nKEY2=VAL2\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        result = await client.query(["KEY1", "KEY2"])
        assert result == {"KEY1": "VAL1", "KEY2": "VAL2"}
        assert len(session.calls) == 1
        assert session.calls[0]["params"] == [
            ("_t", "valid-tok"),
            ("KEY1", ""),
            ("KEY2", ""),
        ]

    @pytest.mark.asyncio
    async def test_query_omitted_key_raises_format_error(self) -> None:
        """Если адаптер опустил запрошенный ключ, поднимается ResponseFormatError."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\nKEY1=VAL1\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        with pytest.raises(ResponseFormatError, match="omitted requested key"):
            await client.query(["KEY1", "MISSING_KEY"])

    @pytest.mark.asyncio
    async def test_post_success_with_active_token(self) -> None:
        """Выполнение post с активным токеном передает параметры формы."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\nRESULT=OK\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        res = await client.post({"COMMAND": "test"})
        assert res == {"RESULT": "OK"}
        assert session.calls[0]["params"] == {"_t": "valid-tok"}
        assert session.calls[0]["data"] == {"COMMAND": "test"}


class TestClientAutoReconnectAndDeadlockFree:
    """Тесты прозрачного обновления протухшего токена и защиты от дедлока."""

    @pytest.mark.asyncio
    async def test_query_relogin_on_token_expiry_no_deadlock(self) -> None:
        """Протухание токена (ERROR=004) в query() выполняет re-login без дедлока."""
        session = _FakeSession(
            [
                (200, "ERROR=004\r\n"),  # 1. Запрос со старым токеном -> 004
                (200, "<html>SPA</html>"),  # 2. Re-login Preflight
                (200, "ERROR=000\r\nTOKEN=fresh-token\r\n"),  # 3. Re-login POST
                (200, "ERROR=000\r\nSYSTEM.PRODUCTION.HW_PRODUCT=PG2400P\r\n"),  # 4. Повтор запроса
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "stale-token"

        result = await asyncio.wait_for(
            client.query(["SYSTEM.PRODUCTION.HW_PRODUCT"]),
            timeout=3.0,
        )
        assert result["SYSTEM.PRODUCTION.HW_PRODUCT"] == "PG2400P"
        assert client._token == "fresh-token"
        assert len(session.calls) == 4

    @pytest.mark.asyncio
    async def test_token_expiry_fails_fast_on_repeated_error(self) -> None:
        """Если после re-login адаптер снова возвращает ERROR=004, поднимается TokenExpiredError."""
        session = _FakeSession(
            [
                (200, "ERROR=004\r\n"),  # 1. Запрос -> 004
                (200, "<html>SPA</html>"),  # 2. Re-login Preflight
                (200, "ERROR=000\r\nTOKEN=token2\r\n"),  # 3. Re-login POST
                (200, "ERROR=004\r\n"),  # 4. Повторный запрос снова 004!
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "token1"

        with pytest.raises(TokenExpiredError):
            await asyncio.wait_for(client.query(["KEY1"]), timeout=3.0)


class TestClientDomainMethods:
    """Тесты высокоуровневых методов предметной области."""

    @pytest.mark.asyncio
    async def test_get_device_info(self) -> None:
        """get_device_info считывает и нормализует поля адаптера."""
        session = _FakeSession(
            [
                (
                    200,
                    (
                        "ERROR=000\r\n"
                        "SYSTEM.PRODUCTION.HW_PRODUCT=PG2400P\r\n"
                        "SYSTEM.PRODUCTION.HW_REVISION=1.0\r\n"
                        "SYSTEM.GENERAL.FW_VERSION=1.0.3 Build 20221213\r\n"
                        "SYSTEM.PRODUCTION.MAC_ADDR=AA:BB:CC:00:11:22\r\n"
                        "NODE.GENERAL.DEVICE_NAME=plc-main\r\n"
                        "NODE.GENERAL.DOMAIN_NAME=HomeGrid\r\n"
                    ),
                ),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        info = await client.get_device_info()
        assert info.model == "PG2400P"
        assert info.hw_revision == "1.0"
        assert info.fw_version == "1.0.3 Build 20221213"
        assert info.mac_address == "aa:bb:cc:00:11:22"
        assert info.device_name == "plc-main"
        assert info.domain_name == "HomeGrid"
        assert info.ip_address == "192.0.2.10"

    @pytest.mark.asyncio
    async def test_get_peers_filters_dummy_and_local_mac(self) -> None:
        """get_peers фильтрует нулевой MAC и MAC локального адаптера, деля rate на 32."""
        session = _FakeSession(
            [
                (
                    200,
                    (
                        "ERROR=000\r\n"
                        "SYSTEM.PRODUCTION.MAC_ADDR=aa:bb:cc:00:11:22\r\n"
                        "DIDMNG.GENERAL.MACS=00:00:00:00:00:00,aa:bb:cc:00:11:22,aa:bb:cc:00:11:33\r\n"
                        "DIDMNG.GENERAL.RX_BPS=0,0,16000\r\n"
                        "DIDMNG.GENERAL.TX_BPS=0,0,12800\r\n"
                    ),
                ),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        peers = await client.get_peers()
        assert len(peers) == 1
        peer = peers[0]
        assert peer.peer_mac == "aa:bb:cc:00:11:33"
        assert peer.rx_raw == 16000
        assert peer.tx_raw == 12800
        assert peer.rx_rate_mbps == 500  # 16000 // 32
        assert peer.tx_rate_mbps == 400  # 12800 // 32
        assert peer.active is True

    @pytest.mark.asyncio
    async def test_get_peers_unequal_lengths_raises(self) -> None:
        """Несовпадение длин списков MAC и скоростей вызывает ResponseFormatError."""
        session = _FakeSession(
            [
                (
                    200,
                    (
                        "ERROR=000\r\n"
                        "SYSTEM.PRODUCTION.MAC_ADDR=aa:bb:cc:00:11:22\r\n"
                        "DIDMNG.GENERAL.MACS=00:00:00:00:00:00,aa:bb:cc:00:11:22\r\n"
                        "DIDMNG.GENERAL.RX_BPS=0\r\n"
                        "DIDMNG.GENERAL.TX_BPS=0,0\r\n"
                    ),
                ),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        with pytest.raises(ResponseFormatError, match="unequal lengths"):
            await client.get_peers()

    @pytest.mark.asyncio
    async def test_get_peers_non_integer_rates_raises(self) -> None:
        """Нечисловые значения в массивах скоростей вызывают ResponseFormatError."""
        session = _FakeSession(
            [
                (
                    200,
                    (
                        "ERROR=000\r\n"
                        "SYSTEM.PRODUCTION.MAC_ADDR=aa:bb:cc:00:11:22\r\n"
                        "DIDMNG.GENERAL.MACS=00:00:00:00:00:00,aa:bb:cc:00:11:22\r\n"
                        "DIDMNG.GENERAL.RX_BPS=0,NaN\r\n"
                        "DIDMNG.GENERAL.TX_BPS=0,0\r\n"
                    ),
                ),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        with pytest.raises(ResponseFormatError, match="non-integer"):
            await client.get_peers()

    @pytest.mark.asyncio
    async def test_set_led(self) -> None:
        """Включение и выключение LED отправляет соответствующие значения."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\n"),  # True -> "1"
                (200, "ERROR=000\r\n"),  # False -> "0"
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        assert await client.set_led(True) is True
        assert session.calls[0]["data"] == {LED_ENABLE_KEY: "YES"}

        assert await client.set_led(False) is True
        assert session.calls[1]["data"] == {LED_ENABLE_KEY: "NO"}

    @pytest.mark.asyncio
    async def test_reboot_success(self) -> None:
        """Перезагрузка отправляет HW_RESET=1 и возвращает True."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        assert await client.reboot() is True
        assert session.calls[0]["data"] == {REBOOT_KEY: "1"}

    @pytest.mark.asyncio
    async def test_reboot_catches_connection_reset(self) -> None:
        """При разрыве сокета во время перезагрузки клиент возвращает True."""
        session = _FakeSession(
            [
                aiohttp.ClientConnectionResetError("Connection reset by peer"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        assert await client.reboot() is True

    @pytest.mark.asyncio
    async def test_get_telemetry_queries_all_keys_sequentially(
        self,
        sample_telemetry_1_dict: dict[str, str],
    ) -> None:
        """get_telemetry запрашивает 20 ключей строго последовательно."""
        scripted: list[tuple[int, str]] = []
        for key in TELEMETRY_KEYS:
            val = sample_telemetry_1_dict[key]
            scripted.append((200, f"ERROR=000\r\n{key}={val}\r\n"))

        session = _FakeSession(scripted)
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "valid-tok"

        snapshot = await client.get_telemetry()
        assert len(session.calls) == len(TELEMETRY_KEYS)
        assert snapshot.attenuation_db == 43.0
        assert snapshot.wire_length_m == 60
        assert snapshot.xput_indicator_mbps == 365

    @pytest.mark.asyncio
    async def test_logout(self) -> None:
        """logout отправляет команду завершения сессии и сбрасывает токен."""
        session = _FakeSession(
            [
                (200, "ERROR=000\r\n"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        client._token = "token-to-close"

        await client.logout()
        assert client.authenticated is False
        assert client._token is None
        assert session.calls[0]["data"] == {"COMMAND": "logout"}
        assert session.calls[0]["params"] == {"_t": "token-to-close"}


class TestClientNetworkErrors:
    """Тесты обработки сетевых сбоев и таймаутов."""

    @pytest.mark.asyncio
    async def test_raw_get_timeout_raises_connection_timeout_error(self) -> None:
        """Таймаут сокета преобразуется в ConnectionTimeoutError."""
        session = _FakeSession(
            [
                TimeoutError("Socket read timeout"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        with pytest.raises(ConnectionTimeoutError, match="Timeout communicating"):
            await client.login()

    @pytest.mark.asyncio
    async def test_raw_get_client_error_raises_connection_error(self) -> None:
        """Сетевая ошибка aiohttp преобразуется в PG2400PConnectionError."""
        session = _FakeSession(
            [
                aiohttp.ClientConnectionError("Host unreachable"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        with pytest.raises(PG2400PConnectionError, match="Network error communicating"):
            await client.login()

    @pytest.mark.asyncio
    async def test_raw_get_http_500_raises_connection_error(self) -> None:
        """HTTP-статус != 200 вызывает PG2400PConnectionError."""
        session = _FakeSession(
            [
                (500, "Internal Server Error"),
            ]
        )
        client = PG2400PClient(host="192.0.2.10", password="pass", session=session)  # type: ignore[arg-type]
        with pytest.raises(PG2400PConnectionError, match="HTTP 500"):
            await client.login()
