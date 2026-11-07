"""Exceptions for the TP-Link PG2400P integration."""

from __future__ import annotations


class PG2400PError(Exception):
    """Base exception for all TP-Link PG2400P integration errors."""


class PG2400PConnectionError(PG2400PError):
    """Network communication error with TP-Link PG2400P adapter."""


class ConnectionTimeoutError(PG2400PConnectionError):
    """Connection or request timeout when communicating with adapter."""


class AuthenticationError(PG2400PError):
    """Authentication error on adapter (invalid password, code ERROR=006)."""

    def __init__(self, message: str, login_attempts: int | None = None) -> None:
        """Initialize authentication error.

        Args:
            message: Textual error description.
            login_attempts: Number of failed login attempts recorded by adapter.
        """
        super().__init__(message)
        self.login_attempts = login_attempts


class TokenExpiredError(PG2400PError):
    """Session token is invalid or expired (code ERROR=004)."""


class ResponseFormatError(PG2400PError):
    """Device response is malformed, empty, or violates protocol grammar."""

    def __init__(
        self,
        message: str,
        line_number: int | None = None,
        raw_line: str | None = None,
    ) -> None:
        """Initialize response format error.

        Args:
            message: Syntax error description.
            line_number: Line number of invalid line (if applicable).
            raw_line: Raw content of invalid line.
        """
        super().__init__(message)
        self.line_number = line_number
        self.raw_line = raw_line
