"""Asynchronous HTTP client for TP-Link PG2400P adapters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Any

import aiohttp

from .const import DEFAULT_TIMEOUT, RATE_SCALE, ZERO_MAC
from .errors import (
    AuthenticationError,
    ConnectionTimeoutError,
    PG2400PConnectionError,
    ResponseFormatError,
    TokenExpiredError,
)
from .models import (
    COMPATIBILITY_MODE_MAP,
    COMPATIBILITY_MODE_REVERSE_MAP,
    PHY_MODE_MAP,
    PHY_MODE_REVERSE_MAP,
    QOS_MODE_MAP,
    QOS_MODE_REVERSE_MAP,
    DeviceInfo,
    PeerLink,
    PortStatus,
    PowerlineSettings,
    TelemetrySnapshot,
    decode_telemetry_snapshot,
)
from .protocol import (
    ERROR_INVALID_PASSWORD,
    ERROR_SESSION_WARMUP,
    ERROR_SUCCESS,
    ERROR_TOKEN_INVALID,
    compute_password_hash,
    parse_response,
)

_LOGGER = logging.getLogger(__name__)

# TP-Link PG2400P protocol constants
LOGIN_PASSWORD_KEY = "TPLINK.GENERAL.LOGIN_PASSWORD"
LOGOUT_COMMAND = "logout"

# Device identification keys
PRODUCT_KEY = "SYSTEM.PRODUCTION.HW_PRODUCT"
HARDWARE_REVISION_KEY = "SYSTEM.PRODUCTION.HW_REVISION"
FIRMWARE_VERSION_KEY = "SYSTEM.GENERAL.FW_VERSION"
LOCAL_MAC_KEY = "SYSTEM.PRODUCTION.MAC_ADDR"
DEVICE_NAME_KEY = "NODE.GENERAL.DEVICE_NAME"
DOMAIN_NAME_KEY = "NODE.GENERAL.DOMAIN_NAME"
IP_ADDRESS_KEY = "TCPIP.IPV4.IP_ADDRESS"

# Peer topology keys
PEER_MACS_KEY = "DIDMNG.GENERAL.MACS"
PEER_RX_RATE_KEY = "DIDMNG.GENERAL.RX_BPS"
PEER_TX_RATE_KEY = "DIDMNG.GENERAL.TX_BPS"

# Control keys
LED_ENABLE_KEY = "TPLINK.LEDSCHEDULE.LED_ENABLE"
REBOOT_KEY = "SYSTEM.GENERAL.HW_RESET"

# Static radio configuration keys
PHY_MODE_KEY = "PHYMNG.GENERAL.RUNNING_PHYMODE_ID"
AUTOMATIC_COMPATIBILITY_KEY = "INTERFMITIGATION.XDSL.ENABLED"

# 20 verified hidden G.hn Wave-2 telemetry keys
TELEMETRY_KEYS: tuple[str, ...] = (
    "DIDMNG.GENERAL.DIDS",
    "DIDMNG.GENERAL.ACTIVE",
    "DIDMNG.GENERAL.AVG_ATTENUATION",
    "DIDMNG.GENERAL.WIRE_LENGTH",
    "FLOWMONITOR.INFO.XPUT_INDICATOR",
    "FLOWMONITOR.STATS.LINK_STATUS_DESC",
    "QOS.STATS.INFO",
    "QOS.STATS.DESC",
    "QOS.STATS.G9962",
    "QOS.STATS.G9962_DESC",
    "QOS.STATS.RX_LLC_ERRORS",
    "QOS.STATS.RX_LLC_ERRORS_DESC",
    "QOS.STATS.CHANNEL_INFO",
    "QOS.STATS.CHANNEL_INFO_DESC",
    "ETHIFDRIVER.STATS.INFO",
    "ETHIFDRIVER.STATS.INFO_DESC",
    "ETHIFDRIVER.STATS.ERRORS",
    "ETHIFDRIVER.STATS.ERRORS_DESC",
    "MASTERSELECTION.STATS.INFO",
    "MASTERSELECTION.STATS.DESC",
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0"
)

_PREFLIGHT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "User-Agent": _BROWSER_USER_AGENT,
}


def _ajax_headers(host: str) -> dict[str, str]:
    """Construct HTTP headers for browser AJAX requests."""
    return {
        "Accept": "text/plain, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.5",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": _BROWSER_USER_AGENT,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": f"http://{host}",
        "Referer": f"http://{host}/",
    }


def _normalize_host(host: str) -> str:
    """Validate and sanitize hostname or IP address."""
    cleaned = host.strip()
    if not cleaned:
        raise ValueError("host must not be empty")
    if "://" in cleaned or any(ch in cleaned for ch in ("/", "?", "#")):
        raise ValueError("host must not include scheme, path, query, or fragment")
    if any(ch.isspace() for ch in cleaned):
        raise ValueError("host must not contain whitespace")
    return cleaned


class PG2400PClient:
    """Asynchronous client for communicating with TP-Link PG2400P adapter."""

    def __init__(
        self,
        host: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize client.

        Args:
            host: IP address or DNS hostname of adapter (e.g. '192.0.2.10').
            password: Administrator web interface password.
            session: External aiohttp ClientSession (e.g. from Home Assistant) or None.
            timeout: Operation timeout in seconds.
        """
        self.host = _normalize_host(host)
        self.password = password
        self.timeout = timeout
        self._session = session
        self._owns_session = session is None
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def authenticated(self) -> bool:
        """Return True if session token has been acquired and stored."""
        return self._token is not None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return existing or create internal aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
            self._owns_session = True
        return self._session

    async def __aenter__(self) -> PG2400PClient:
        """Enter asynchronous context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit asynchronous context manager and release resources."""
        await self.close()

    async def close(self) -> None:
        """End active adapter session and close session if owned by client."""
        try:
            if self._token is not None:
                await self.logout()
        except Exception as exc:
            _LOGGER.debug("Error logging out during client close on %s: %s", self.host, exc)
        finally:
            if self._owns_session and self._session is not None and not self._session.closed:
                await self._session.close()

    # --- Authentication and session token handling ---

    async def login(self) -> str:
        """Public login: acquire lock and obtain session token."""
        async with self._lock:
            return await self._do_login()

    async def _do_login(self) -> str:
        """Internal login without acquiring self._lock (must be called with lock held)."""
        session = await self._get_session()
        base_url = f"http://{self.host}/"

        # 1. Preflight GET /
        await self._raw_get(session, base_url, headers=_PREFLIGHT_HEADERS, expect_payload=False)

        # 2. Login POST / with password hash
        body = {LOGIN_PASSWORD_KEY: compute_password_hash(self.password)}
        headers = _ajax_headers(self.host)
        status, values = await self._raw_post(session, base_url, data=body, headers=headers)

        # 3. Session warmup retry on ERROR=004
        if status == ERROR_SESSION_WARMUP:
            _LOGGER.debug(
                "Device %s returned ERROR=004 on initial login, retrying warmup...",
                self.host,
            )
            await self._raw_get(session, base_url, headers=_PREFLIGHT_HEADERS, expect_payload=False)
            status, values = await self._raw_post(session, base_url, data=body, headers=headers)

        # 4. Check response status
        if status == ERROR_INVALID_PASSWORD:
            attempts_raw = values.get("LOGIN_TIMES")
            attempts = int(attempts_raw) if attempts_raw and attempts_raw.isdigit() else None
            detail = f" (failed attempts: {attempts})" if attempts else ""
            raise AuthenticationError(
                f"Device {self.host} rejected password{detail}",
                login_attempts=attempts,
            )

        if status != ERROR_SUCCESS:
            raise ResponseFormatError(f"Login failed on {self.host} with error code {status}")

        token = values.get("TOKEN")
        if not token:
            raise ResponseFormatError(f"Login response on {self.host} missing TOKEN")

        self._token = token
        _LOGGER.debug("Successfully authenticated to %s with token", self.host)
        return token

    async def logout(self) -> None:
        """Terminate active session on adapter via COMMAND=logout."""
        async with self._lock:
            if self._token is None:
                return
            token = self._token
            self._token = None
            try:
                session = await self._get_session()
                base_url = f"http://{self.host}/"
                headers = _ajax_headers(self.host)
                await self._raw_post(
                    session,
                    base_url,
                    data={"COMMAND": LOGOUT_COMMAND},
                    params={"_t": token},
                    headers=headers,
                )
            except Exception as exc:
                _LOGGER.debug("Logout failed on %s: %s", self.host, exc)

    # --- Base requests and automatic re-authentication ---

    async def query(self, keys: list[str]) -> dict[str, str]:
        """Query key values via GET /?_t=<token>&KEY1=&KEY2=..."""
        if not keys:
            return {}

        async with self._lock:
            session = await self._get_session()
            base_url = f"http://{self.host}/"
            headers = _ajax_headers(self.host)

            async def _run_query(token: str) -> tuple[str, dict[str, str]]:
                params = [("_t", token)] + [(k, "") for k in keys]
                return await self._raw_get(session, base_url, params=params, headers=headers)

            values = await self._execute_with_reauth(_run_query)

            missing = [k for k in keys if k not in values]
            if missing:
                raise ResponseFormatError(
                    f"Device {self.host} omitted requested key(s): {', '.join(missing)}"
                )
            return values

    async def post(self, body: dict[str, str] | str) -> dict[str, str]:
        """Send form POST request to /?_t=<token>."""
        async with self._lock:
            session = await self._get_session()
            base_url = f"http://{self.host}/"
            headers = _ajax_headers(self.host)

            async def _run_post(token: str) -> tuple[str, dict[str, str]]:
                params = {"_t": token}
                return await self._raw_post(
                    session,
                    base_url,
                    data=body,
                    params=params,
                    headers=headers,
                )

            return await self._execute_with_reauth(_run_post)

    async def _execute_with_reauth(
        self,
        operation: Callable[[str], Coroutine[Any, Any, tuple[str, dict[str, str]]]],
    ) -> dict[str, str]:
        """Execute operation, logging in or re-authenticating if required.

        Must be called with self._lock held.
        """
        if self._token is None:
            await self._do_login()

        assert self._token is not None
        status, values = await operation(self._token)

        # On expired token (ERROR=004), retry login exactly once
        if status == ERROR_TOKEN_INVALID:
            _LOGGER.debug("Token expired on %s (ERROR=004), re-authenticating...", self.host)
            await self._do_login()
            assert self._token is not None
            status, values = await operation(self._token)
            if status == ERROR_TOKEN_INVALID:
                raise TokenExpiredError(f"Session token expired on {self.host} and relogin failed")

        if status != ERROR_SUCCESS:
            raise ResponseFormatError(f"Operation failed on {self.host} with device error {status}")

        return values

    # --- Low-level HTTP handlers ---

    async def _raw_get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: list[tuple[str, str]] | None = None,
        headers: dict[str, str] | None = None,
        expect_payload: bool = True,
    ) -> tuple[str, dict[str, str]]:
        """Low-level GET request with network exception handling."""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with session.get(url, params=params, headers=headers, timeout=timeout) as resp:
                if resp.status != 200:
                    raise PG2400PConnectionError(f"HTTP {resp.status} from {url}")
                text = await resp.text()
                if not expect_payload or headers == _PREFLIGHT_HEADERS:
                    return ERROR_SUCCESS, {}
                return parse_response(text)
        except TimeoutError as exc:
            raise ConnectionTimeoutError(f"Timeout communicating with {self.host}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise PG2400PConnectionError(
                f"Network error communicating with {self.host}: {exc}"
            ) from exc

    async def _raw_post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        data: dict[str, str] | str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Low-level POST request with network exception handling."""
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            async with session.post(
                url, data=data, params=params, headers=headers, timeout=timeout
            ) as resp:
                if resp.status != 200:
                    raise PG2400PConnectionError(f"HTTP {resp.status} from {url}")
                text = await resp.text()
                return parse_response(text)
        except TimeoutError as exc:
            raise ConnectionTimeoutError(f"Timeout communicating with {self.host}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise PG2400PConnectionError(
                f"Network error communicating with {self.host}: {exc}"
            ) from exc

    # --- High-level domain methods ---

    async def get_device_info(self) -> DeviceInfo:
        """Fetch device identification and hardware parameters."""
        keys = [
            PRODUCT_KEY,
            HARDWARE_REVISION_KEY,
            FIRMWARE_VERSION_KEY,
            LOCAL_MAC_KEY,
            DEVICE_NAME_KEY,
            DOMAIN_NAME_KEY,
        ]
        values = await self.query(keys)
        return DeviceInfo(
            model=values[PRODUCT_KEY],
            hw_revision=values[HARDWARE_REVISION_KEY],
            fw_version=values[FIRMWARE_VERSION_KEY],
            mac_address=values[LOCAL_MAC_KEY].lower(),
            device_name=values[DEVICE_NAME_KEY],
            domain_name=values[DOMAIN_NAME_KEY],
            ip_address=self.host,
        )

    async def get_peers(self) -> list[PeerLink]:
        """Fetch remote peer links, excluding fictitious zero and local MAC."""
        keys = [
            LOCAL_MAC_KEY,
            PEER_MACS_KEY,
            PEER_RX_RATE_KEY,
            PEER_TX_RATE_KEY,
        ]
        values = await self.query(keys)
        local_mac = values[LOCAL_MAC_KEY].lower()

        macs = [m.strip().lower() for m in values[PEER_MACS_KEY].split(",") if m.strip()]
        try:
            rx_rates = [int(r.strip()) for r in values[PEER_RX_RATE_KEY].split(",") if r.strip()]
            tx_rates = [int(r.strip()) for r in values[PEER_TX_RATE_KEY].split(",") if r.strip()]
        except ValueError as exc:
            raise ResponseFormatError("Peer rate array contains non-integer values") from exc

        if not (len(macs) == len(rx_rates) == len(tx_rates)):
            raise ResponseFormatError("Peer MAC, RX and TX rate arrays have unequal lengths")

        peers: list[PeerLink] = []
        for mac, rx_raw, tx_raw in zip(macs, rx_rates, tx_rates, strict=True):
            if mac in (ZERO_MAC, local_mac):
                continue
            peers.append(
                PeerLink(
                    peer_mac=mac,
                    rx_rate_mbps=rx_raw // RATE_SCALE,
                    tx_rate_mbps=tx_raw // RATE_SCALE,
                    rx_raw=rx_raw,
                    tx_raw=tx_raw,
                    active=True,
                )
            )
        return peers

    async def get_telemetry(self) -> TelemetrySnapshot:
        """Fetch 20 hidden G.hn Wave-2 telemetry fields.

        Queries are executed sequentially key by key (query([key])) to avoid
        buffer overflow on the Xtensa microcontroller.
        """
        fields: dict[str, str] = {}
        for key in TELEMETRY_KEYS:
            res = await self.query([key])
            fields[key] = res[key]
        return decode_telemetry_snapshot(fields, host=self.host)

    async def get_port_status(self) -> PortStatus:
        """Retrieve Ethernet port connection status and speeds."""
        res = await self.post({"COMMAND": "lan link speed"})
        try:
            p1 = int(res.get("PORT1", 0))
            p2 = int(res.get("PORT2", 0))
        except (ValueError, TypeError):
            p1, p2 = 0, 0
        return PortStatus.from_codes(p1, p2)

    async def get_led_state(self) -> bool | None:
        """Retrieve the current LED indicator state."""
        res = await self.query([LED_ENABLE_KEY])
        val = res.get(LED_ENABLE_KEY, "").strip().upper()
        if val in ("1", "ON", "TRUE", "YES"):
            return True
        if val in ("0", "OFF", "FALSE", "NO"):
            return False
        return None

    async def set_led(self, enabled: bool) -> bool:
        """Turn adapter LED status indicators on or off."""
        val = "YES" if enabled else "NO"
        res = await self.post({LED_ENABLE_KEY: val})
        return res.get("ERROR", ERROR_SUCCESS) == ERROR_SUCCESS

    async def get_powerline_settings(self) -> PowerlineSettings:
        """Fetch static radio parameters and adapter profiles."""
        res_keys = await self.query([PHY_MODE_KEY, AUTOMATIC_COMPATIBILITY_KEY])
        raw_phy = res_keys.get(PHY_MODE_KEY, "").strip()
        raw_xdsl = res_keys.get(AUTOMATIC_COMPATIBILITY_KEY, "").strip()

        compat_res = await self.post({"COMMAND": "get compatibility mode"})
        raw_compat = compat_res.get("RESULT", "").strip()

        qos_res = await self.post({"COMMAND": "get qos"})
        raw_qos = qos_res.get("RESULT", "").strip()

        phy_mode = PHY_MODE_MAP.get(raw_phy, f"Unknown ({raw_phy})")
        compatibility_mode = COMPATIBILITY_MODE_MAP.get(raw_compat, f"Unknown ({raw_compat})")
        qos_mode = QOS_MODE_MAP.get(raw_qos, f"Unknown ({raw_qos})")
        xdsl_mitigation = raw_xdsl.upper() == "YES"

        return PowerlineSettings(
            phy_mode=phy_mode,
            compatibility_mode=compatibility_mode,
            qos_mode=qos_mode,
            xdsl_mitigation=xdsl_mitigation,
        )

    async def set_automatic_compatibility(self, enabled: bool) -> bool:
        """Turn automatic xDSL compatibility mitigation on or off."""
        val = "YES" if enabled else "NO"
        res = await self.post({AUTOMATIC_COMPATIBILITY_KEY: val})
        return res.get("ERROR", ERROR_SUCCESS) == ERROR_SUCCESS

    async def set_phy_mode(self, mode: str) -> bool:
        """Set radio PHY mode (MIMO / SISO)."""
        code = PHY_MODE_REVERSE_MAP.get(mode)
        if not code:
            raise ValueError(f"Unknown PHY mode: {mode}")
        res = await self.post({PHY_MODE_KEY: code})
        return res.get("ERROR", ERROR_SUCCESS) == ERROR_SUCCESS

    async def set_compatibility_mode(self, mode: str) -> bool:
        """Set VDSL compatibility profile."""
        code = COMPATIBILITY_MODE_REVERSE_MAP.get(mode)
        if not code:
            raise ValueError(f"Unknown VDSL compatibility mode: {mode}")
        res = await self.post({"COMMAND": f"set compatibility mode {code}"})
        return res.get("RESULT", "0") == "0"

    async def set_qos_mode(self, mode: str) -> bool:
        """Set QoS profile."""
        code = QOS_MODE_REVERSE_MAP.get(mode)
        if not code:
            raise ValueError(f"Unknown QoS mode: {mode}")
        res = await self.post({"COMMAND": f"set qos {code}"})
        return res.get("RESULT", "0") == "0"

    async def reboot(self) -> bool:
        """Reboot adapter via SYSTEM.GENERAL.HW_RESET=1.

        During reboot, the socket connection may sever abruptly; the client
        catches the connection error and returns True.
        """
        try:
            res = await self.post({REBOOT_KEY: "1"})
            return res.get("ERROR", ERROR_SUCCESS) == ERROR_SUCCESS
        except (ConnectionTimeoutError, PG2400PConnectionError):
            _LOGGER.info("Device %s severed connection on reboot as expected", self.host)
            return True


__all__ = [
    "AUTOMATIC_COMPATIBILITY_KEY",
    "DEFAULT_TIMEOUT",
    "DEVICE_NAME_KEY",
    "DOMAIN_NAME_KEY",
    "FIRMWARE_VERSION_KEY",
    "HARDWARE_REVISION_KEY",
    "IP_ADDRESS_KEY",
    "LED_ENABLE_KEY",
    "LOCAL_MAC_KEY",
    "LOGIN_PASSWORD_KEY",
    "LOGOUT_COMMAND",
    "PEER_MACS_KEY",
    "PEER_RX_RATE_KEY",
    "PEER_TX_RATE_KEY",
    "PG2400PClient",
    "PHY_MODE_KEY",
    "PRODUCT_KEY",
    "RATE_SCALE",
    "REBOOT_KEY",
    "TELEMETRY_KEYS",
    "ZERO_MAC",
]
