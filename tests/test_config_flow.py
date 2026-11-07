"""Тесты для мастера настройки (Config Flow, Reauth, Options) TP-Link PG2400P."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.config_flow import PG2400POptionsFlow
from custom_components.currentlink.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.currentlink.errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    ResponseFormatError,
)
from custom_components.currentlink.models import DeviceInfo

TEST_HOST = "192.0.2.10"
TEST_PASSWORD = "secret_password"
TEST_MAC = "aa:bb:cc:00:11:22"
TEST_DEVICE_NAME = "plc-main"

MOCK_DEVICE_INFO = DeviceInfo(
    model="PG2400P",
    hw_revision="1.0",
    fw_version="1.0.3 Build 20221213 Rel.62540",
    mac_address=TEST_MAC,
    device_name=TEST_DEVICE_NAME,
    domain_name="HomeNetwork",
    ip_address=TEST_HOST,
)


@pytest.fixture(autouse=True)
def bypass_setup_entry():
    """Предотвращает реальный запуск setup_entry при создании ConfigEntry в тестах."""
    with patch(
        "custom_components.currentlink.async_setup_entry",
        return_value=True,
        create=True,
    ):
        yield


def _mock_client(
    login_result: str = "test_token_123",
    device_info: DeviceInfo = MOCK_DEVICE_INFO,
    login_error: Exception | None = None,
    info_error: Exception | None = None,
):
    """Создает мок клиента PG2400PClient с заданным поведением."""
    client = AsyncMock()
    if login_error:
        client.login.side_effect = login_error
    else:
        client.login.return_value = login_result

    if info_error:
        client.get_device_info.side_effect = info_error
    else:
        client.get_device_info.return_value = device_info

    client.close.return_value = None
    return client


# =========================================================================
# 1. Тесты основного шага настройки (async_step_user)
# =========================================================================


async def test_user_step_shows_initial_form(hass: HomeAssistant) -> None:
    """Проверяет, что при открытии формы отображаются поля host и password."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_step_success(hass: HomeAssistant) -> None:
    """Проверяет успешное создание записи конфигурации при корректных учетных данных."""
    client = _mock_client()

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"TP-Link PG2400P ({TEST_DEVICE_NAME})"
    assert result["data"] == {
        CONF_HOST: TEST_HOST,
        CONF_PASSWORD: TEST_PASSWORD,
    }
    assert result["result"].unique_id == TEST_MAC

    client.login.assert_awaited_once()
    client.get_device_info.assert_awaited_once()
    client.close.assert_awaited_once()


async def test_user_step_success_fallback_title_to_mac(hass: HomeAssistant) -> None:
    """Проверяет заголовок записи по умолчанию (MAC-адрес), если имя устройства пустое."""
    info_no_name = DeviceInfo(
        model="PG2400P",
        hw_revision="1.0",
        fw_version="1.0.3",
        mac_address=TEST_MAC,
        device_name="",
        domain_name="HomeNetwork",
        ip_address=TEST_HOST,
    )
    client = _mock_client(device_info=info_no_name)

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"TP-Link PG2400P ({TEST_MAC})"


async def test_user_step_cleans_url_prefixes(hass: HomeAssistant) -> None:
    """Проверяет очистку префикса http:// и замыкающего слэша в адресе хоста."""
    client = _mock_client()

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ) as mock_cls:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "http://192.0.2.10/", CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == TEST_HOST
    # Проверяем, что в конструктор передан нормализованный IP
    assert mock_cls.call_args[0][0] == TEST_HOST


async def test_user_step_cleans_uppercase_url_prefixes(hass: HomeAssistant) -> None:
    """Проверяет очистку префикса HTTP:// в верхнем регистре."""
    client = _mock_client()

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ) as mock_cls:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: "HTTP://192.0.2.10/", CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == TEST_HOST
    assert mock_cls.call_args[0][0] == TEST_HOST


async def test_user_step_invalid_host_unmocked_returns_cannot_connect(
    hass: HomeAssistant,
) -> None:
    """Проверяет обработку некорректного хоста реальным конструктором PG2400PClient без падения."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_HOST: "http://192.0.2.10/foo/bar", CONF_PASSWORD: TEST_PASSWORD},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_step_invalid_auth(hass: HomeAssistant) -> None:
    """Проверяет обработку неверного пароля (AuthenticationError -> invalid_auth)."""
    client = _mock_client(
        login_error=AuthenticationError("Device rejected password", login_attempts=1)
    )

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "wrong_password"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_auth"}
    client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "error",
    [
        PG2400PConnectionError("Connection refused"),
        ConnectionTimeoutError("Timed out waiting for response"),
        TimeoutError(),
    ],
)
async def test_user_step_cannot_connect(hass: HomeAssistant, error: Exception) -> None:
    """Проверяет обработку сетевых ошибок и таймаутов (cannot_connect)."""
    client = _mock_client(login_error=error)

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    client.close.assert_awaited_once()


async def test_user_step_unknown_exception(hass: HomeAssistant) -> None:
    """Проверяет обработку непредвиденных исключений (unknown)."""
    client = _mock_client(login_error=ResponseFormatError("Garbled status line"))

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unknown"}
    client.close.assert_awaited_once()


async def test_user_step_duplicate_unique_id_aborts(hass: HomeAssistant) -> None:
    """Проверяет прерывание настройки при попытке добавить уже настроенный адаптер."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: "192.0.2.99", CONF_PASSWORD: "old_password"},
    )
    entry.add_to_hass(hass)

    client = _mock_client()

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# =========================================================================
# 2. Тесты повторной аутентификации (async_step_reauth)
# =========================================================================


async def test_reauth_flow_success(hass: HomeAssistant) -> None:
    """Проверяет успешное обновление пароля через поток реаутентификации."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old_bad_password"},
        title="TP-Link PG2400P (plc-main)",
    )
    entry.add_to_hass(hass)

    client = _mock_client()

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PASSWORD: "new_good_password"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new_good_password"


async def test_reauth_flow_invalid_auth(hass: HomeAssistant) -> None:
    """Проверяет ошибку invalid_auth при неверном пароле в reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old_bad_password"},
        title="TP-Link PG2400P",
    )
    entry.add_to_hass(hass)

    client = _mock_client(login_error=AuthenticationError("Device rejected password"))

    result = await entry.start_reauth_flow(hass)
    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PASSWORD: "still_bad_password"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm"
    assert result2["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old_bad_password"


async def test_reauth_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Проверяет ошибку cannot_connect при сетевом сбое во время reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old_password"},
    )
    entry.add_to_hass(hass)

    client = _mock_client(login_error=PG2400PConnectionError("Host unreachable"))

    result = await entry.start_reauth_flow(hass)
    with patch(
        "custom_components.currentlink.config_flow.PG2400PClient",
        return_value=client,
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_PASSWORD: "new_password"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "reauth_confirm"
    assert result2["errors"] == {"base": "cannot_connect"}


# =========================================================================
# 3. Тесты потока опций (OptionsFlowHandler)
# =========================================================================


async def test_options_flow_init(hass: HomeAssistant) -> None:
    """Проверяет отображение формы параметров с текущим интервалом опроса."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        options={CONF_SCAN_INTERVAL: 45},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_update_scan_interval(hass: HomeAssistant) -> None:
    """Проверяет сохранение нового интервала опроса через Options Flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_SCAN_INTERVAL: 60},
    )

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"] == {CONF_SCAN_INTERVAL: 60}
    assert entry.options == {CONF_SCAN_INTERVAL: 60}


@pytest.mark.parametrize(
    "boundary_interval",
    [
        MIN_SCAN_INTERVAL,  # 15
        MAX_SCAN_INTERVAL,  # 300
    ],
)
async def test_options_flow_boundary_intervals_accepted(
    hass: HomeAssistant, boundary_interval: int
) -> None:
    """Проверяет корректное сохранение граничных значений интервала (15 и 300 с)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={CONF_SCAN_INTERVAL: boundary_interval},
    )

    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"] == {CONF_SCAN_INTERVAL: boundary_interval}
    assert entry.options == {CONF_SCAN_INTERVAL: boundary_interval}


@pytest.mark.parametrize(
    "invalid_input",
    [
        {CONF_SCAN_INTERVAL: 10},
        {CONF_SCAN_INTERVAL: 0},
        {CONF_SCAN_INTERVAL: -5},
        {CONF_SCAN_INTERVAL: 500},
        {CONF_SCAN_INTERVAL: "invalid"},
        {CONF_SCAN_INTERVAL: None},
        {},
    ],
)
async def test_options_flow_invalid_scan_interval_returns_form_error(
    hass: HomeAssistant, invalid_input: dict[str, Any]
) -> None:
    """Проверяет отображение ошибки invalid_scan_interval при некорректном интервале."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_MAC,
        data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
    )
    entry.add_to_hass(hass)

    flow = PG2400POptionsFlow(entry)
    flow.hass = hass

    result = await flow.async_step_init(invalid_input)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "invalid_scan_interval"}
