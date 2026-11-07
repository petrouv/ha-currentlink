"""Protocol parser and cryptographic utilities for TP-Link PG2400P."""

from __future__ import annotations

from hashlib import md5

from .errors import ResponseFormatError

# TP-Link PG2400P protocol status codes
ERROR_SUCCESS = "000"
ERROR_TOKEN_INVALID = "004"
ERROR_SESSION_WARMUP = "004"
ERROR_INVALID_PASSWORD = "006"
ERROR_UNSUPPORTED = "009"


def compute_password_hash(password: str) -> str:
    """Compute MD5 hash for TP-Link PG2400P authentication.

    The device firmware expects an MD5 hash of the password formatted as a
    32-character lowercase hexadecimal string.

    Args:
        password: Plaintext password in UTF-8 encoding.

    Returns:
        32-character lowercase hex string of the MD5 hash.

    Raises:
        TypeError: If password is not a string.
    """
    if not isinstance(password, str):
        raise TypeError(f"Password must be str, got {type(password).__name__}")
    return md5(password.encode("utf-8"), usedforsecurity=False).hexdigest().lower()


def parse_response(text: str) -> tuple[str, dict[str, str]]:
    """Parse raw key-value response payload from TP-Link PG2400P adapter.

    The device response consists of CRLF/LF delimited text lines. The first
    non-empty line must contain the status code formatted as 'ERROR=<code>'.
    Subsequent lines contain 'KEY=VALUE' pairs.

    Args:
        text: Raw HTTP response text from device.

    Returns:
        Tuple of (error_code, key_value_dict) where:
            - error_code: String error code (e.g., "000", "004", "006", "009").
            - key_value_dict: Dictionary of parsed response keys excluding 'ERROR'.

    Raises:
        TypeError: If text is not a string.
        ResponseFormatError: If payload is empty, lacks 'ERROR=' header, contains
            lines without '=' separator, has empty keys, or has duplicate keys.
    """
    if not isinstance(text, str):
        raise TypeError(f"Response text must be str, got {type(text).__name__}")

    lines = text.splitlines()
    non_empty_lines: list[tuple[int, str]] = []
    for line_idx, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if stripped:
            non_empty_lines.append((line_idx, stripped))

    if not non_empty_lines:
        raise ResponseFormatError("Empty response body from device")

    first_line_number, first_line = non_empty_lines[0]
    if "=" not in first_line:
        raise ResponseFormatError(
            f"First response line has no key/value separator: {first_line!r}",
            line_number=first_line_number,
            raw_line=first_line,
        )

    first_key, first_val = first_line.split("=", maxsplit=1)
    first_key = first_key.strip()
    first_val = first_val.strip()

    if first_key != "ERROR":
        raise ResponseFormatError(
            f"Response missing ERROR status header on first line, got {first_key!r}",
            line_number=first_line_number,
            raw_line=first_line,
        )

    if not first_val:
        raise ResponseFormatError(
            "Response status ERROR line contains an empty error code",
            line_number=first_line_number,
            raw_line=first_line,
        )

    error_code = first_val
    data: dict[str, str] = {}

    for line_number, line in non_empty_lines[1:]:
        if "=" not in line:
            raise ResponseFormatError(
                f"Response line {line_number} has no key/value separator: {line!r}",
                line_number=line_number,
                raw_line=line,
            )

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()

        if not key:
            raise ResponseFormatError(
                f"Response line {line_number} has an empty key",
                line_number=line_number,
                raw_line=line,
            )

        if key in data:
            raise ResponseFormatError(
                f"Response line {line_number} contains duplicate key {key!r}",
                line_number=line_number,
                raw_line=line,
            )

        data[key] = value

    return error_code, data
