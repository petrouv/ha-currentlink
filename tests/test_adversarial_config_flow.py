"""Adversarial stress-tests for PG2400PConfigFlow and PG2400POptionsFlow.

Tests both normal contract compliance and empirical reproduction of failure modes.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.currentlink.config_flow import (
    PG2400POptionsFlow,
)
from custom_components.currentlink.const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from custom_components.currentlink.errors import (
    AuthenticationError,
    PG2400PConnectionError,
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
    """Bypass actual setup_entry when creating ConfigEntry in tests."""
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
    """Create a mock PG2400PClient with mock login, get_device_info, and close."""
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


# =============================================================================
# 1. Host Input Handling & Bug Reproduction
# =============================================================================


class TestHostInputHandling:
    """Stress-test host variations in config flow."""

    @pytest.mark.parametrize(
        ("input_host", "expected_saved_host"),
        [
            ("http://192.0.2.10", "192.0.2.10"),
            ("https://192.0.2.10/", "192.0.2.10"),
            ("192.0.2.10:80", "192.0.2.10:80"),
            ("  192.0.2.10  ", "192.0.2.10"),
            ("http://192.0.2.10:80/", "192.0.2.10:80"),
            ("https://192.0.2.10:8080", "192.0.2.10:8080"),
            ("plc-main.local", "plc-main.local"),
            ("  http://plc-main.local/  ", "plc-main.local"),
            ("[fe80::1]", "[fe80::1]"),
            ("fe80::1", "fe80::1"),
            ("[::1]", "[::1]"),
            ("::1", "::1"),
            ("http://[fe80::1]/", "[fe80::1]"),
            ("http://[::1]:80/", "[::1]:80"),
        ],
    )
    async def test_valid_or_normalizable_hosts_succeed(
        self, hass: HomeAssistant, input_host: str, expected_saved_host: str
    ) -> None:
        """Verify that valid or normalizable host inputs result in successful entry creation."""
        client = _mock_client()

        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ) as mock_client_cls:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_HOST: input_host, CONF_PASSWORD: TEST_PASSWORD},
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_HOST] == expected_saved_host
        assert mock_client_cls.call_args[0][0] == expected_saved_host
        client.close.assert_awaited_once()

    @pytest.mark.parametrize(
        "invalid_host",
        [
            "",
            "   ",
            "http://",
            "https://",
            "http:///",
            "http://192.0.2.10/foo/bar",
            "192.0.2.10:80#frag",
            "192.0.2.10?param=1",
            "192.0.2.10 with space",
            "http://invalid host with spaces/",
            "http://192.0.2.10:80/subpath",
        ],
    )
    async def test_invalid_host_inputs_handled_without_unhandled_exception(
        self, hass: HomeAssistant, invalid_host: str
    ) -> None:
        """Verify invalid host inputs return a form with cannot_connect error."""
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={CONF_HOST: invalid_host, CONF_PASSWORD: TEST_PASSWORD},
        )

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}


# =============================================================================
# 2. Password Variations
# =============================================================================


class TestPasswordHandling:
    """Stress-test password variations in config flow."""

    @pytest.mark.parametrize(
        "password",
        [
            "",  # empty password
            "   ",  # whitespace only
            "  secret  ",  # password with surrounding whitespace
            "пароль123",  # cyrillic unicode
            "🔑secret🔒!#$",  # emoji and symbols
            "!@#$%^&*()_+-=[]{}|;':\",./<>?`~",  # all ASCII punctuation
            "pass\twith\ttabs",  # tabs
            "multi\nline\npassword",  # newlines
            "a" * 256,  # long password
        ],
    )
    async def test_password_variations_preserved(self, hass: HomeAssistant, password: str) -> None:
        """Verify password edge cases are passed without corruption to client and stored."""
        client = _mock_client()

        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ) as mock_client_cls:
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_HOST: TEST_HOST, CONF_PASSWORD: password},
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"][CONF_PASSWORD] == password
        assert mock_client_cls.call_args[0][1] == password
        client.close.assert_awaited_once()


# =============================================================================
# 3. Duplicate Unique ID Abort
# =============================================================================


class TestDuplicateUniqueId:
    """Stress-test duplicate unique_id abort logic."""

    async def test_duplicate_mac_aborts_immediately(self, hass: HomeAssistant) -> None:
        """Verify configuring an adapter with existing MAC aborts with already_configured."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: "old"},
            title="TP-Link PG2400P (plc-main)",
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
                data={CONF_HOST: "192.0.2.99", CONF_PASSWORD: "new_password"},
            )

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        client.close.assert_awaited_once()

    async def test_distinct_mac_allows_second_adapter(self, hass: HomeAssistant) -> None:
        """Verify multiple adapters with distinct MAC addresses can be configured."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: "password"},
            title="TP-Link PG2400P (plc-main)",
        )
        entry.add_to_hass(hass)

        info2 = DeviceInfo(
            model="PG2400P",
            hw_revision="1.0",
            fw_version="1.0.3",
            mac_address="aa:bb:cc:00:11:33",
            device_name="plc-remote",
            domain_name="HomeNetwork",
            ip_address="192.0.2.11",
        )
        client2 = _mock_client(device_info=info2)

        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client2,
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_USER},
                data={CONF_HOST: "192.0.2.11", CONF_PASSWORD: "remote_password"},
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == "aa:bb:cc:00:11:33"
        assert result["title"] == "TP-Link PG2400P (plc-remote)"


# =============================================================================
# 4. Reauth Flow Lifecycle & Edge Cases
# =============================================================================


class TestReauthFlow:
    """Stress-test reauth flow edge cases and entry updates."""

    async def test_reauth_updates_password_and_reloads_entry(self, hass: HomeAssistant) -> None:
        """Verify reauth updates credentials in-place without duplicating entries."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "expired_password"},
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
                user_input={CONF_PASSWORD: "new_valid_password"},
            )

        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "new_valid_password"
        # Entry count remains strictly 1 (no duplicate entries)
        entries = hass.config_entries.async_entries(DOMAIN)
        assert len(entries) == 1
        client.close.assert_awaited_once()

    async def test_reauth_with_unicode_password(self, hass: HomeAssistant) -> None:
        """Verify reauth works with unicode password."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old"},
            title="TP-Link PG2400P",
        )
        entry.add_to_hass(hass)

        client = _mock_client()

        result = await entry.start_reauth_flow(hass)
        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_PASSWORD: "новый_пароль_🔑_2026"},
            )

        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"
        assert entry.data[CONF_PASSWORD] == "новый_пароль_🔑_2026"

    async def test_reauth_invalid_password_shows_form_error(self, hass: HomeAssistant) -> None:
        """Verify reauth with invalid password displays form with invalid_auth error."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old"},
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
                user_input={CONF_PASSWORD: "wrong_password"},
            )

        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "invalid_auth"}
        client.close.assert_awaited_once()

    async def test_reauth_connection_error_shows_form_error(self, hass: HomeAssistant) -> None:
        """Verify reauth with connection error displays form with cannot_connect error."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old"},
        )
        entry.add_to_hass(hass)

        client = _mock_client(login_error=PG2400PConnectionError("Device unreachable"))

        result = await entry.start_reauth_flow(hass)
        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_PASSWORD: "password"},
            )

        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "reauth_confirm"
        assert result2["errors"] == {"base": "cannot_connect"}
        client.close.assert_awaited_once()

    async def test_reauth_does_not_verify_mac_changed_risk(self, hass: HomeAssistant) -> None:
        """ADVERSARIAL OBSERVATION: Reauth flow does not verify if MAC address changed.

        If an adapter at TEST_HOST is physically replaced with a different MAC,
        `async_step_reauth_confirm` does not compare `_info.mac_address` against
        `reauth_entry.unique_id`, blindly updating the entry for a different physical device.
        """
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old"},
        )
        entry.add_to_hass(hass)

        # Device at TEST_HOST now reports a DIFFERENT MAC address
        different_info = DeviceInfo(
            model="PG2400P",
            hw_revision="1.0",
            fw_version="1.0.3",
            mac_address="aa:bb:cc:dd:ee:ff",
            device_name="different-adapter",
            domain_name="HomeNetwork",
            ip_address=TEST_HOST,
        )
        client = _mock_client(device_info=different_info)

        result = await entry.start_reauth_flow(hass)
        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_PASSWORD: "new_password"},
            )

        # Notice: It succeeds without aborting on MAC mismatch!
        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "reauth_successful"


# =============================================================================
# 5. Options Flow Boundaries & Invalid Types
# =============================================================================


class TestOptionsFlow:
    """Stress-test options flow boundary values, schema, and invalid types."""

    @pytest.mark.parametrize(
        "valid_interval",
        [
            MIN_SCAN_INTERVAL,  # 15
            16,
            DEFAULT_SCAN_INTERVAL,  # 30
            120,
            299,
            MAX_SCAN_INTERVAL,  # 300
        ],
    )
    async def test_options_flow_valid_intervals_accepted(
        self, hass: HomeAssistant, valid_interval: int
    ) -> None:
        """Verify valid intervals within [15, 300] are accepted by options flow."""
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
            user_input={CONF_SCAN_INTERVAL: valid_interval},
        )

        assert result2["type"] is FlowResultType.CREATE_ENTRY
        assert result2["data"][CONF_SCAN_INTERVAL] == valid_interval
        assert entry.options[CONF_SCAN_INTERVAL] == valid_interval

    @pytest.mark.parametrize(
        "out_of_bounds_interval",
        [
            10,  # below MIN_SCAN_INTERVAL (15)
            0,  # zero
            -1,  # negative
            500,  # above MAX_SCAN_INTERVAL (300)
            1000,
        ],
    )
    async def test_options_flow_schema_enforces_boundaries_via_data_entry_flow(
        self, hass: HomeAssistant, out_of_bounds_interval: int
    ) -> None:
        """Verify Home Assistant Data Entry Flow schema rejects out-of-bounds scan intervals."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        # Voluptuous schema validation in async_configure raises InvalidData for out-of-bounds
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={CONF_SCAN_INTERVAL: out_of_bounds_interval},
            )

    @pytest.mark.parametrize(
        "boundary_value",
        [
            10,
            0,
            -1,
            500,
            1000,
        ],
    )
    async def test_options_flow_step_init_rejects_out_of_bounds_when_called_directly(
        self, hass: HomeAssistant, boundary_value: int
    ) -> None:
        """Verify async_step_init rejects out-of-bounds scan intervals when invoked directly."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        entry.add_to_hass(hass)

        flow = PG2400POptionsFlow(entry)
        flow.hass = hass

        result = await flow.async_step_init({CONF_SCAN_INTERVAL: boundary_value})
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {"base": "invalid_scan_interval"}

    @pytest.mark.parametrize(
        "malformed_input",
        [
            {CONF_SCAN_INTERVAL: "not_a_number"},
            {CONF_SCAN_INTERVAL: None},
            {},
        ],
    )
    async def test_options_flow_step_init_handles_malformed_input_gracefully(
        self, hass: HomeAssistant, malformed_input: dict[str, Any]
    ) -> None:
        """Verify async_step_init returns form with invalid_scan_interval on malformed input."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: TEST_PASSWORD},
            options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
        )
        flow = PG2400POptionsFlow(entry)
        flow.hass = hass

        result = await flow.async_step_init(malformed_input)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
        assert result["errors"] == {"base": "invalid_scan_interval"}


# =============================================================================
# 6. Resource Discipline & Leaks
# =============================================================================


class TestResourceDiscipline:
    """Verify client session and token slots are always freed."""

    async def test_client_closed_on_validation_failure(self, hass: HomeAssistant) -> None:
        """Verify client.close() is called even when login fails with unknown error."""
        client = _mock_client(login_error=RuntimeError("Unexpected crash during handshake"))

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
        assert result["errors"] == {"base": "unknown"}
        client.close.assert_awaited_once()

    async def test_client_closed_on_reauth_failure(self, hass: HomeAssistant) -> None:
        """Verify client.close() is called on reauth validation failure."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=TEST_MAC,
            data={CONF_HOST: TEST_HOST, CONF_PASSWORD: "old"},
        )
        entry.add_to_hass(hass)

        client = _mock_client(login_error=RuntimeError("Reauth network dropped"))

        result = await entry.start_reauth_flow(hass)
        with patch(
            "custom_components.currentlink.config_flow.PG2400PClient",
            return_value=client,
        ):
            result2 = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                user_input={CONF_PASSWORD: "new"},
            )

        assert result2["type"] is FlowResultType.FORM
        assert result2["errors"] == {"base": "unknown"}
        client.close.assert_awaited_once()
