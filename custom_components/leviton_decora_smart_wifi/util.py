"""Utilities for Leviton Decora Smart Wi-Fi integration."""

from typing import Any

from .const import DOMAIN

# Keys that must not be written back onto a config entry from a login body.
_LOGIN_RESPONSE_DROP = {"code", "password"}


def generate_device_identifier(identifier: int | str) -> tuple[str, str]:
    """Generate device identifier."""
    return (DOMAIN, str(identifier))


def login_response_for_storage(response: dict[str, Any]) -> dict[str, Any]:
    """Copy a login response without one-time secrets.

    The bearer (``id``) is kept because the WebSocket authenticates with the
    full login response. The password and the one-time 2FA code are dropped
    so they are not persisted for reuse.
    """
    cleaned = {
        key: value
        for key, value in response.items()
        if key not in _LOGIN_RESPONSE_DROP
    }
    user = cleaned.get("user")
    if isinstance(user, dict):
        cleaned["user"] = {
            key: value for key, value in user.items() if key not in _LOGIN_RESPONSE_DROP
        }
    return cleaned
