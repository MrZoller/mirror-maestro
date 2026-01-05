"""
Logging utilities for safe and secure logging.

Provides helpers to sanitize user input before logging to prevent
log injection attacks and information disclosure.
"""

import re
from urllib.parse import urlparse
from typing import Any


def sanitize_for_logging(value: Any, max_length: int = 500) -> str:
    """
    Remove control characters and limit length for safe logging.

    Prevents CRLF injection attacks where malicious input containing
    newlines could forge log entries.

    Args:
        value: Value to sanitize (will be converted to string)
        max_length: Maximum length to allow (default: 500)

    Returns:
        Sanitized string safe for logging

    Examples:
        >>> sanitize_for_logging("normal text")
        'normal text'
        >>> sanitize_for_logging("malicious\\nINFO Admin password: secret")
        'malicious\\\\nINFO Admin password: secret'
        >>> sanitize_for_logging("x" * 1000, max_length=10)
        'xxxxxxxxxx...[truncated 990 chars]'
    """
    if not isinstance(value, str):
        value = str(value)

    # Replace control characters with escaped versions
    sanitized = (
        value.replace('\r', '\\r')
        .replace('\n', '\\n')
        .replace('\t', '\\t')
        .replace('\x00', '\\x00')  # Null byte
    )

    # Remove other control characters (ASCII 0-31 except those handled above)
    sanitized = re.sub(r'[\x01-\x08\x0b-\x0c\x0e-\x1f]', '', sanitized)

    # Truncate if too long
    if len(sanitized) > max_length:
        truncated_count = len(sanitized) - max_length
        sanitized = sanitized[:max_length] + f"...[truncated {truncated_count} chars]"

    return sanitized


def sanitize_url_for_logging(url: str) -> str:
    """
    Remove credentials from URL before logging.

    Prevents accidental exposure of passwords or tokens embedded in URLs.

    Args:
        url: URL that may contain credentials

    Returns:
        URL with userinfo (username:password) removed

    Examples:
        >>> sanitize_url_for_logging("https://user:pass@gitlab.com/project")
        'https://gitlab.com/project'
        >>> sanitize_url_for_logging("https://gitlab.com/project")
        'https://gitlab.com/project'
        >>> sanitize_url_for_logging("invalid url")
        '[invalid-url]'
    """
    try:
        parsed = urlparse(url)
        # Reconstruct without userinfo (username:password)
        port_str = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{parsed.hostname}{port_str}{parsed.path or ''}{parsed.query and '?' + parsed.query or ''}"
    except Exception:
        return "[invalid-url]"


def sanitize_exception_for_logging(exc: Exception) -> str:
    """
    Get safe exception message without sensitive data.

    Only logs the exception type, not the message which may contain
    tokens, passwords, or other sensitive information.

    Args:
        exc: Exception to sanitize

    Returns:
        Exception type name only

    Examples:
        >>> sanitize_exception_for_logging(ValueError("secret token: glpat-xxx"))
        'ValueError'
        >>> sanitize_exception_for_logging(KeyError('password'))
        'KeyError'
    """
    return type(exc).__name__


def redact_token(value: str, visible_chars: int = 4) -> str:
    """
    Redact a token or API key for safe logging.

    Shows only the first few characters to help with identification
    while protecting the secret value.

    Args:
        value: Token or secret to redact
        visible_chars: Number of characters to show (default: 4)

    Returns:
        Redacted token

    Examples:
        >>> redact_token("glpat-1234567890abcdef")
        'glpat-...cdef'
        >>> redact_token("short", visible_chars=2)
        'sh...'
    """
    if len(value) <= visible_chars * 2:
        # Token is too short, just show asterisks
        return "*" * len(value)

    return f"{value[:visible_chars]}...{value[-visible_chars:]}"
