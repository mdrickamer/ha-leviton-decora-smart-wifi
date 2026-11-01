"""Shared fixtures for Leviton tests. No network access."""

from typing import Any

from homeassistant.const import (
    CONF_CODE,
    CONF_EMAIL,
    CONF_ID,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TOKEN,
)

from custom_components.leviton_decora_smart_wifi.api import LevitonData
from custom_components.leviton_decora_smart_wifi.api.const import LoginResult
from custom_components.leviton_decora_smart_wifi.api.residence import Residence
from custom_components.leviton_decora_smart_wifi.const import (
    CONF_DEVICES,
    CONF_LOGIN_RESPONSE,
    CONF_RESIDENCES,
    DOMAIN,
)

EMAIL = "pat.secret@example.com"
PASSWORD = "p@ss-secret-value"
TOKEN = "tok-secret-value"
NEW_TOKEN = "tok-refreshed-secret"
CODE = "654321"
USER_ID = "acct-secret-id"
RESIDENCE_ACCOUNT_ID = "resacct-secret-id"
SERIAL = "SN-SECRET-001"
MAC = "aa:bb:cc:dd:ee:ff"
RESIDENCE_ID = 10
DEVICE_ID = 101


def device_payload(
    device_id: int,
    *,
    mac: str | None = MAC,
    name: str = "Porch",
    serial: str = SERIAL,
) -> dict[str, Any]:
    """Return a minimal supported switch payload."""
    return {
        "id": device_id,
        "name": name,
        "model": "DW15S",
        "mac": mac,
        "serial": serial,
        "power": "OFF",
        "connected": True,
        "residenceId": RESIDENCE_ID,
        "manufacturer": "Leviton Manufacturing Co., Inc.",
        "version": "1.2.3",
    }


def build_data(api: Any, devices: list[dict[str, Any]] | None = None) -> LevitonData:
    """Build coordinator data from plain dicts."""
    residence = Residence(
        api,
        {
            "id": RESIDENCE_ID,
            "name": "Home",
            "locality": "Town",
            "region": "ST",
            "status": "HOME",
            "residentialAccountId": RESIDENCE_ACCOUNT_ID,
            "devices": devices if devices is not None else [device_payload(DEVICE_ID)],
            "activities": [],
            "rooms": [],
            "schedules": [],
        },
    )
    return LevitonData({"residences": [residence], "firmware": {}})


def entry_data(**overrides: Any) -> dict[str, Any]:
    """Return config entry data that includes every secret the tests check for."""
    data = {
        CONF_EMAIL: EMAIL,
        CONF_PASSWORD: PASSWORD,
        CONF_TOKEN: TOKEN,
        CONF_CODE: CODE,
        CONF_ID: USER_ID,
        CONF_NAME: "Pat Lee",
        CONF_RESIDENCES: [RESIDENCE_ID],
        CONF_DEVICES: [DEVICE_ID],
        CONF_LOGIN_RESPONSE: {
            "id": TOKEN,
            "userId": USER_ID,
            "password": PASSWORD,
            "code": CODE,
            "user": {
                "id": USER_ID,
                "email": EMAIL,
                "firstName": "Pat",
                "lastName": "Lee",
            },
        },
    }
    data.update(overrides)
    return data


class ApiBehavior:
    """In-memory stand-in for LevitonAPI.update and LevitonAPI.login."""

    def __init__(self) -> None:
        """Initialize."""
        self.update_error: Exception | None = None
        self.devices: list[dict[str, Any]] | None = None
        self.new_authorization: str | None = None
        self.login_result = LoginResult.SUCCESS
        self.login_calls: list[dict[str, Any]] = []

    def update(self, api: Any, target_residences: list[int] | None = None) -> LevitonData:
        """Return fixture data or raise the configured error."""
        if self.new_authorization:
            api.authorization = self.new_authorization
            api.user_id = api.user_id or USER_ID
            api.login_response = {
                "id": self.new_authorization,
                "userId": api.user_id,
                "user": {
                    "id": api.user_id,
                    "email": EMAIL,
                    "firstName": "Pat",
                    "lastName": "Lee",
                },
            }
        if self.update_error is not None:
            raise self.update_error
        return build_data(api, self.devices)

    def login(
        self, api: Any, email: str, password: str, code: str | None = None
    ) -> LoginResult:
        """Record the login attempt and apply a successful session when asked."""
        self.login_calls.append(
            {"email": email, "password": password, "code": code}
        )
        if self.login_result == LoginResult.CODE_REQUIRED and not code:
            return LoginResult.CODE_REQUIRED
        if self.login_result not in (LoginResult.SUCCESS, LoginResult.CODE_REQUIRED):
            return self.login_result
        api.authorization = NEW_TOKEN
        api.user_id = USER_ID
        api.user_name = "Pat Lee"
        api.login_response = {
            "id": NEW_TOKEN,
            "userId": USER_ID,
            "user": {
                "id": USER_ID,
                "email": email,
                "firstName": "Pat",
                "lastName": "Lee",
            },
        }
        api.credentials = {"email": email, "password": password}
        return LoginResult.SUCCESS


def integration_log_text(caplog: Any) -> str:
    """Return log messages emitted by this integration."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith(f"custom_components.{DOMAIN}")
        or record.name.startswith("homeassistant.components.diagnostics")
    )
