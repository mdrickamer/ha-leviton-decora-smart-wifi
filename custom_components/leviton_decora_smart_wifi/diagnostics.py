"""Diagnostics support for Leviton Decora Smart Wi-Fi."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DOMAIN

TO_REDACT = {
    CONF_CODE,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
    "accountId",
    "account_id",
    "authorization",
    "email",
    "firstName",
    "lastName",
    "localIP",
    "mac",
    "macAddress",
    "name",
    "password",
    "residentialAccountId",
    "residential_account_id",
    "serial",
    "serialNumber",
    "serial_number",
    "title",
    "token",
    "unique_id",
    "userId",
    "userName",
    "user_id",
    "user_name",
    "username",
    # Account id on the config entry, login response, and device payloads.
    "id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry with secrets removed."""
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "data": _coordinator_payload(coordinator.data),
        },
        TO_REDACT,
    )


def _coordinator_payload(data: Any) -> dict[str, Any] | None:
    """Serialize the latest account snapshot for diagnostics."""
    if data is None:
        return None
    residences = []
    for residence in data.residences:
        residences.append(
            {
                "id": residence.id,
                "name": residence.name,
                "residentialAccountId": getattr(residence, "residential_account_id", None),
                "devices": [
                    {
                        "id": device.id,
                        "mac": device.mac,
                        "name": device.name,
                        "serial": device.serial,
                    }
                    for device in residence.devices
                ],
            }
        )
    return {"residences": residences}
