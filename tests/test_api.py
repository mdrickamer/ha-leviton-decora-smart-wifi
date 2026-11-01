"""Unit tests for the Leviton API client. The HTTP session is mocked."""

from http import HTTPMethod
import json
import threading
import time

import pytest
import requests

from custom_components.leviton_decora_smart_wifi.api import (
    LevitonAPI,
    LevitonAuthError,
    LevitonException,
)
from custom_components.leviton_decora_smart_wifi.api.const import LoginResult


def _response(status: int, payload: object) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.encoding = "utf-8"
    response._content = json.dumps(payload).encode()
    return response


def test_login_does_not_retain_two_factor_code(monkeypatch) -> None:
    """A successful interactive login must not keep the one-time code."""
    api = LevitonAPI()

    def fake_call(method, url, headers=None, **kwargs):
        assert kwargs["data"]["code"] == "999111"
        return {
            "id": "session-token",
            "user": {"id": "user-1", "firstName": "A", "lastName": "B"},
        }

    monkeypatch.setattr(api, "call", fake_call)

    assert api.login("a@b.c", "pw", "999111") == LoginResult.SUCCESS
    assert api.credentials == {"email": "a@b.c", "password": "pw"}
    assert "code" not in api.credentials


def test_refresh_does_not_replay_two_factor_code() -> None:
    """Automatic re-login sends the password and not the stored 2FA code."""
    api = LevitonAPI(
        authorization="old-token",
        user_id="acct",
        credentials={
            "email": "a@b.c",
            "password": "pw",
            "code": "111111",
        },
    )
    posts: list[dict] = []

    def fake_request(method, url, headers=None, **kwargs):
        if url.endswith("person/login"):
            posts.append(kwargs.get("data") or {})
            return _response(
                200,
                {
                    "id": "new-token",
                    "user": {"id": "acct", "firstName": "A", "lastName": "B"},
                },
            )
        if headers and headers.get("authorization") == "old-token":
            return _response(
                401,
                {
                    "error": {
                        "statusCode": 401,
                        "name": "Authorization",
                        "message": "Authorization Required",
                    }
                },
            )
        return _response(200, [])

    api.session.request = fake_request

    assert api.call(HTTPMethod.GET, "person/acct/residentialpermissions") == []
    assert posts == [{"email": "a@b.c", "password": "pw"}]
    assert api.authorization == "new-token"
    assert "code" not in api.credentials


def test_refresh_failed_login_raises_auth_error() -> None:
    """A rejected re-login is not ignored."""
    api = LevitonAPI(
        authorization="old-token",
        user_id="acct",
        credentials={"email": "a@b.c", "password": "pw", "code": "111111"},
    )

    def fake_request(method, url, headers=None, **kwargs):
        if url.endswith("person/login"):
            assert "code" not in (kwargs.get("data") or {})
            return _response(
                401,
                {"error": {"statusCode": 401, "name": "Error", "message": "Login Failed"}},
            )
        return _response(
            401,
            {
                "error": {
                    "statusCode": 401,
                    "name": "Authorization",
                    "message": "Authorization Required",
                }
            },
        )

    api.session.request = fake_request

    with pytest.raises(LevitonAuthError):
        api.call(HTTPMethod.GET, "person/acct/residentialpermissions")


def test_update_propagates_auth_and_server_errors(monkeypatch) -> None:
    """update() must not swallow auth failures or HTTP 5xx."""
    api = LevitonAPI(authorization="tok", user_id="acct")

    def server_error(*_args, **_kwargs):
        raise LevitonException(502, "BadGateway", "bad gateway")

    monkeypatch.setattr(api, "get_residences", server_error)
    with pytest.raises(LevitonException) as err:
        api.update()
    assert err.value.status_code == 502

    def auth_error(*_args, **_kwargs):
        raise LevitonAuthError(LoginResult.CODE_REQUIRED)

    monkeypatch.setattr(api, "get_residences", auth_error)
    with pytest.raises(LevitonAuthError):
        api.update()


def test_save_response_redacts_login_secrets(tmp_path) -> None:
    """The debug dump must not contain a login token or password."""
    api = LevitonAPI(save_location=str(tmp_path), authorization="old-token")
    api.save_response(
        {
            "id": "brand-new-token",
            "password": "secret-pass",
            "user": {"id": "acct-1", "email": "a@b.c", "code": "123456"},
        },
        name="person/login",
    )
    text = (tmp_path / "person_login.json").read_text(encoding="utf-8")
    assert "brand-new-token" not in text
    assert "secret-pass" not in text
    assert "123456" not in text

    api.authorization = "live-token"
    api.save_response({"note": "live-token", "id": 5}, name="residences/1/iotswitches")
    dumped = (tmp_path / "residences_1_iotswitches.json").read_text(encoding="utf-8")
    assert "live-token" not in dumped
    assert "5" in dumped


def test_session_calls_are_serialized() -> None:
    """Shared requests.Session use is locked so calls cannot overlap."""
    api = LevitonAPI(authorization="tok", user_id="acct")
    state = {"active": 0, "max": 0}

    def fake_request(method, url, headers=None, **kwargs):
        state["active"] += 1
        state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        state["active"] -= 1
        return _response(200, {})

    api.session.request = fake_request
    threads = [
        threading.Thread(target=lambda: api.call(HTTPMethod.GET, "person/acct"))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert state["max"] == 1
