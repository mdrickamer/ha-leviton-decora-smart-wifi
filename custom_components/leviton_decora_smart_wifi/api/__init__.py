"""Leviton API."""

from collections.abc import Callable
from http import HTTPMethod
import json
import logging
from pathlib import Path
import threading
from typing import Any

import requests

from .const import API_ENDPOINT, FIRMWARE_APP_MAP, FirmwareAppID, LoginResult
from .firmware import Firmware
from .residence import Residence

_LOGGER = logging.getLogger(__name__)

# (connect, read) seconds applied to every request. Without a timeout a hung
# connection blocks the whole update cycle until the coordinator gives up,
# marking every entity unavailable for a full scan interval.
REQUEST_TIMEOUT = (5, 10)

_REDACTED = "**REDACTED**"
# Written by the "save responses" debug option. These keys (and any value
# equal to the live bearer) are replaced so a login token or password cannot
# land on disk.
_SECRET_KEYS = frozenset(
    {
        "accesstoken",
        "access_token",
        "authorization",
        "code",
        "password",
        "refresh_token",
        "refreshtoken",
        "token",
    }
)


class LevitonData:
    """LevitonData."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        """Initialize."""
        self.data = data if data is not None else {}

    @property
    def firmware(self) -> dict[str, Firmware]:
        """Firmware."""
        return self.data.get("firmware", {})

    @property
    def residences(self) -> list[Residence]:
        """Residences."""
        return self.data.get("residences", [])


class LevitonException(Exception):
    """LevitonException."""

    def __init__(self, status_code: int, name: str, message: str) -> None:
        """Initialize."""
        super().__init__()
        self.status_code = status_code
        self.name = name
        self.message = message
        # Expected faults (expired token, 2FA, 5xx, lockout) used to be logged
        # at ERROR on every poll. Callers decide the user-visible severity.
        _LOGGER.debug(
            "\n- LevitonException\n- Status: %s\n- Name: %s\n- Message: %s",
            self.status_code,
            self.name,
            self.message,
        )


class LevitonAuthError(LevitonException):
    """Raised when the token is rejected and re-login cannot succeed.

    A stored 2FA code is single use, so replaying it after the token expires
    fails and each attempt counts toward Leviton's lockout. Callers turn this
    into ConfigEntryAuthFailed and ask the user to sign in again.
    """

    def __init__(self, result: LoginResult | None = None) -> None:
        """Initialize."""
        self.result = result
        super().__init__(
            status_code=401,
            name="AuthenticationFailed",
            message=(
                f"Re-login failed: {result}"
                if result
                else "Token rejected and no credentials stored"
            ),
        )


class LevitonAPI:
    """LevitonAPI."""

    def __init__(
        self,
        authorization: str | None = None,
        save_location: str | None = None,
        user_id: str | None = None,
        credentials: dict | None = None,
    ) -> None:
        """Initialize."""
        self.authorization = authorization
        self.save_location = save_location
        self.user_id = user_id

        self.credentials: dict = {
            key: value
            for key, value in (credentials or {}).items()
            if key != "code" and value is not None
        }
        self.is_logging_in: bool = False
        self.data: LevitonData = LevitonData()
        self.session = requests.Session()
        # requests.Session is shared by polling and service calls and is not
        # thread-safe. The lock covers one request at a time. It is not held
        # across refresh()'s re-login, which calls back into call().
        # A per-command partial refresh (instead of rebuilding the whole
        # account on coordinator.async_request_refresh) was considered and
        # intentionally not implemented; state still arrives on the next poll
        # or websocket push.
        self._session_lock = threading.Lock()
        self.user_name: str | None = None
        self.login_response: dict[str, Any] | None = None

    def call(
        self,
        method: HTTPMethod,
        url: str,
        headers: dict | None = None,
        **kwargs,
    ) -> list[dict] | dict[str, Any] | None:
        """Call."""
        if headers is None:
            headers = {}
        _LOGGER.debug("Calling API with method: %s and URL: %s", method, url)

        # Bound every request so one slow endpoint cannot stall the batch.
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)

        # Build the authorization header at send time. A retry that follows a
        # re-login must use the new token, so it cannot be baked in up front.
        def send_request() -> requests.Response:
            request_headers = dict(headers)
            if self.authorization:
                request_headers["authorization"] = self.authorization
            with self._session_lock:
                return self.session.request(
                    method=method,
                    url=f"{API_ENDPOINT}/{url}",
                    headers=request_headers,
                    **kwargs,
                )

        response = self.refresh(send_request)
        response = self.parse_response(response=response)
        self.save_response(response=response, name=url)
        return response

    def login(self, email: str, password: str, code: str | None = None) -> LoginResult:
        """Login."""
        # A rejected login is a 401 itself. is_logging_in stops refresh()
        # from treating that 401 as an expired token and logging in again.
        self.is_logging_in = True
        try:
            return self._login(email, password, code)
        finally:
            self.is_logging_in = False

    def _login(self, email: str, password: str, code: str | None) -> LoginResult:
        """Send the login request and classify the result."""
        try:
            data = {"email": email, "password": password}
            if code:
                data["code"] = code
            response = self.call(
                method=HTTPMethod.POST,
                url="person/login",
                params={"include": "user"},
                data=data,
            )
            if response and isinstance(response, dict):
                self.authorization = response["id"]
                self.user_id = response["user"]["id"]
                self.user_name = "{} {}".format(
                    response["user"]["firstName"],
                    response["user"]["lastName"],
                )
                self.login_response = response
        except LevitonException as exception:
            if all(
                [
                    exception.status_code == 401,
                    exception.message == "Login Failed",
                ]
            ):
                return LoginResult.FAILED
            if all(
                [
                    exception.status_code == 403,
                    exception.message == "Too many failed attempts",
                ]
            ):
                return LoginResult.TOO_MANY_ATTEMPTS
            if all(
                [
                    exception.status_code == 406,
                    exception.message
                    == "Insufficient Data: Person uses two factor authentication. Requires code.",
                ]
            ):
                return LoginResult.CODE_REQUIRED
            if all(
                [
                    exception.status_code == 408,
                    exception.message == "Error: Invalid code",
                ]
            ):
                return LoginResult.CODE_INVALID
            return LoginResult.FAILED
        # The 2FA code is single use. Keep the password so a later refresh can
        # re-login, but never the code.
        self.credentials = {"email": email, "password": password}
        return LoginResult.SUCCESS

    def decode_body(self, response: requests.Response) -> Any:
        """Decode a JSON body, returning None when the body is not JSON.

        The API intermittently answers with an HTML error page or an empty
        body. Calling json.loads on that raises JSONDecodeError, which
        aborts the whole coordinator update and marks every entity
        unavailable until the next poll.
        """
        try:
            return json.loads(response.text)
        except ValueError:
            return None

    def parse_response(self, response: requests.Response) -> dict[str, Any] | None:
        """Parse the response."""
        text = self.decode_body(response)

        if response.status_code != 200 or text is None:
            error = text.get("error", {}) if isinstance(text, dict) else {}
            raise LevitonException(
                status_code=error.get("statusCode", response.status_code),
                name=error.get("name", "InvalidResponse"),
                message=error.get(
                    "message", f"Non-JSON response: {response.text[:200]!r}"
                ),
            )

        return text

    def refresh(self, function: Callable) -> requests.Response:
        """Refresh login authorization, retrying once on a stale connection.

        Leviton's REST endpoint silently closes pooled keep-alive
        connections; the next request on a stale connection fails with
        ``ConnectionError``/``RemoteDisconnected`` and HA marks every
        coordinator-bound entity unavailable until the next cycle. Retry
        once after rotating the requests.Session so the client gets a
        fresh socket. The same retry covers read/connect timeouts, and a
        non-JSON error body is retried once before being reported.
        """
        try:
            response = function()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            _LOGGER.debug(
                "Leviton REST connection dropped or timed out; retrying with"
                " fresh session"
            )
            self._reset_session()
            response = function()

        if response.status_code != 200:
            text = self.decode_body(response)

            # A non-JSON error body is a transient cloud fault. Retry once
            # here rather than failing the whole update cycle.
            if text is None:
                _LOGGER.debug(
                    "Leviton returned a non-JSON body (status %s); retrying once",
                    response.status_code,
                )
                response = function()
                text = self.decode_body(response)

            error = text.get("error", {}) if isinstance(text, dict) else {}

            # Re-login on any 401. The API returns "Authorization Required"
            # rather than "Invalid Access Token", so matching on the message
            # text never fired and an expired token was reused indefinitely.
            # is_logging_in stops recursion, since login() calls call().
            # The stored 2FA code is not sent: it is single use, and replaying
            # it only burns attempts toward the account lockout.
            if response.status_code == 401 and not self.is_logging_in:
                email = self.credentials.get("email")
                password = self.credentials.get("password")
                if not email or not password:
                    raise LevitonAuthError

                _LOGGER.debug(
                    "Leviton rejected the token (%s); re-authenticating",
                    error.get("message"),
                )
                result = self.login(email=email, password=password)
                if result != LoginResult.SUCCESS:
                    raise LevitonAuthError(result)

                response = function()
                if response.status_code == 401:
                    raise LevitonAuthError(LoginResult.FAILED)

        return response

    def _reset_session(self) -> None:
        """Replace the pooled session after a dropped connection."""
        with self._session_lock:
            self.session.close()
            self.session = requests.Session()

    def save_response(
        self, response: dict[str, Any] | None, name: str = "response"
    ) -> None:
        """Save the response to a file."""
        if self.save_location and response:
            if not Path(self.save_location).is_dir():
                _LOGGER.debug("Creating directory: %s", self.save_location)
                Path(self.save_location).mkdir()
            safe_name = name.replace("/", "_").replace(".", "_")
            file_path_name = f"{self.save_location}/{safe_name}.json"
            _LOGGER.debug("Saving response: %s", file_path_name)
            payload = self._redact_saved_payload(
                response, login_response="login" in name
            )
            with Path(file_path_name).open(mode="w", encoding="utf-8") as file:
                json.dump(
                    obj=payload,
                    fp=file,
                    indent=4,
                    default=lambda o: "not-serializable",
                    sort_keys=True,
                )

    def _redact_saved_payload(
        self, value: Any, *, login_response: bool = False
    ) -> Any:
        """Remove login tokens and passwords before a debug dump is written."""
        if isinstance(value, dict):
            redacted: dict[str, Any] = {}
            for key, item in value.items():
                if self._is_saved_secret(key, item, login_bearer=login_response):
                    redacted[key] = _REDACTED
                else:
                    redacted[key] = self._redact_saved_payload(item)
            return redacted
        if isinstance(value, list):
            return [self._redact_saved_payload(item) for item in value]
        if isinstance(value, str) and self.authorization and value == self.authorization:
            return _REDACTED
        return value

    def _is_saved_secret(self, key: Any, item: Any, *, login_bearer: bool) -> bool:
        """Return True if this field would persist a login secret."""
        if str(key).lower() in _SECRET_KEYS:
            return True
        # person/login returns the bearer as top-level "id", and that id is
        # not yet stored on self.authorization when the response is saved.
        if login_bearer and key == "id":
            return True
        return bool(
            isinstance(item, str) and self.authorization and item == self.authorization
        )

    def update(self, target_residences: list[int] | None = None) -> LevitonData:
        """Update."""
        try:
            data = {}
            data["residences"] = self.get_residences(target_residences)
            data["firmware"] = self.get_firmware(data["residences"])
            self.data = LevitonData(data)
        except LevitonAuthError:
            # Returning stale data here hides an expired login forever.
            raise
        except LevitonException as exception:
            # 5xx (and a token that is still rejected after re-login) must
            # reach the coordinator. Swallowing them used to look like a
            # successful poll with empty or stale data.
            if exception.status_code == 401 or exception.status_code >= 500:
                if exception.status_code == 401:
                    raise LevitonAuthError from exception
                raise
            _LOGGER.debug(
                "Leviton update failed with status %s: %s",
                exception.status_code,
                exception.message,
            )
            return self.data
        return self.data

    def get_residences(
        self, target_residences: list[int] | None = None
    ) -> list[Residence]:
        """Get residences."""
        data = []
        permissions = self.call(
            method=HTTPMethod.GET,
            url=f"person/{self.user_id}/residentialpermissions",
        )
        if permissions and isinstance(permissions, list):
            for permission in permissions:
                residential_account_id = permission["residentialAccountId"]
                residences = self.call(
                    method=HTTPMethod.GET,
                    url=f"residentialaccounts/{residential_account_id}/residences",
                )
                if residences and isinstance(residences, list):
                    for residence in residences:
                        if residence and isinstance(residence, dict):
                            residence_id = residence["id"]
                            if any(
                                [
                                    target_residences is None,
                                    target_residences
                                    and residence_id in target_residences,
                                ]
                            ):
                                residence["activities"] = self.call(
                                    method=HTTPMethod.GET,
                                    url=f"residences/{residence_id}/residentialactivities",
                                )
                                residence["devices"] = self.call(
                                    method=HTTPMethod.GET,
                                    url=f"residences/{residence_id}/iotswitches",
                                    headers={
                                        "filter": json.dumps(
                                            obj={"include": ["iotButtons"]}
                                        )
                                    },
                                )
                                residence["rooms"] = self.call(
                                    method=HTTPMethod.GET,
                                    url=f"residences/{residence_id}/residentialrooms",
                                    headers={
                                        "filter": json.dumps(
                                            obj={"include": ["residentialScenes"]}
                                        )
                                    },
                                )
                                residence["schedules"] = self.call(
                                    method=HTTPMethod.GET,
                                    url=f"residences/{residence_id}/residentialschedules",
                                )
                                data.append(Residence(self, residence))
        return data

    def get_firmware(self, residences: list[Residence]) -> dict[str, Firmware]:
        """Get firmware."""
        devices: dict[str, FirmwareAppID] = {}
        for residence in residences:
            for device in residence.devices:
                if device.model and device.model not in devices:
                    devices[device.model] = FIRMWARE_APP_MAP[device.generation]

        firmware: dict[str, Firmware] = {}
        for model, app_id in devices.items():
            app_firmware = self.call(
                method=HTTPMethod.GET,
                url="lcsapps/getfirmware",
                params={
                    "appId": app_id,
                    "model": model,
                    "data": json.dumps(
                        {
                            "condensed": False,
                        }
                    ).encode("ascii"),
                },
            )
            if app_firmware and isinstance(app_firmware, list):
                firmware[model] = Firmware(app_firmware[0])
        return firmware
