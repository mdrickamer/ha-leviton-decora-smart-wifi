"""Integration tests with a mocked Leviton client and no network access."""

import logging

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_CODE, CONF_PASSWORD, CONF_TOKEN, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leviton_decora_smart_wifi import (
    async_remove_config_entry_device,
)
from custom_components.leviton_decora_smart_wifi.api import (
    LevitonAuthError,
    LevitonException,
)
from custom_components.leviton_decora_smart_wifi.api.const import LoginResult
from custom_components.leviton_decora_smart_wifi.const import (
    CONF_DEVICES,
    DATA_COORDINATOR,
    DOMAIN,
)
from custom_components.leviton_decora_smart_wifi.diagnostics import (
    async_get_config_entry_diagnostics,
)

from tests.common import (
    CODE,
    DEVICE_ID,
    EMAIL,
    MAC,
    NEW_TOKEN,
    PASSWORD,
    RESIDENCE_ACCOUNT_ID,
    RESIDENCE_ID,
    SERIAL,
    TOKEN,
    USER_ID,
    device_payload,
    entry_data,
    integration_log_text,
)

LEGACY_MAC = "AA:BB:CC:DD:EE:FF"
MISSING_MAC_LEGACY = "ZZ:YY:XX:WW:VV:UU"


def _entry(**kwargs) -> MockConfigEntry:
    data = entry_data()
    if "data" in kwargs:
        data = kwargs.pop("data")
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        unique_id=kwargs.pop("unique_id", USER_ID),
        title=kwargs.pop("title", "Pat Lee"),
        version=kwargs.pop("version", 2),
        **kwargs,
    )


async def _setup(hass, entry, api_behavior) -> bool:
    entry.add_to_hass(hass)
    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return result


def _switch_entity_id(hass, unique_id: str) -> str | None:
    registry = er.async_get(hass)
    return registry.async_get_entity_id("switch", DOMAIN, unique_id)


async def test_setup_success(hass, api_behavior) -> None:
    """A mocked account creates entities and leaves them available."""
    entry = _entry()

    assert await _setup(hass, entry, api_behavior)
    assert entry.state is ConfigEntryState.LOADED

    entity_id = _switch_entity_id(hass, f"{RESIDENCE_ID}-{DEVICE_ID}-switch")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


async def test_auth_failure_at_setup_starts_reauth(hass, api_behavior) -> None:
    """An auth failure during setup raises ConfigEntryAuthFailed and opens reauth."""
    api_behavior.update_error = LevitonAuthError(LoginResult.FAILED)
    entry = _entry()

    assert not await _setup(hass, entry, api_behavior)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == SOURCE_REAUTH for flow in flows)


async def test_reauth_success_updates_entry_and_drops_code(hass, api_behavior) -> None:
    """Reauth stores the new password and bearer and does not store the 2FA code."""
    entry = _entry()
    entry.add_to_hass(hass)
    api_behavior.login_result = LoginResult.CODE_REQUIRED

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": USER_ID,
        },
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "authenticate"
    assert api_behavior.login_calls[-1]["code"] is None

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "999000"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
    assert CONF_CODE not in entry.data
    assert "999000" not in str(entry.data)
    assert api_behavior.login_calls[-1]["code"] == "999000"


async def test_login_error_key_matches_strings(hass, api_behavior) -> None:
    """Failed login uses the translated login_failed key."""
    api_behavior.login_result = LoginResult.FAILED
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"email": EMAIL, "password": PASSWORD},
    )
    assert result["errors"]["base"] == "login_failed"


@pytest.mark.parametrize(
    "error",
    [
        LevitonException(502, "BadGateway", "bad gateway"),
        TimeoutError(),
    ],
)
async def test_first_refresh_not_ready(hass, api_behavior, error) -> None:
    """A boot-time 502 or timeout asks Home Assistant to retry setup."""
    api_behavior.update_error = error
    entry = _entry()

    assert not await _setup(hass, entry, api_behavior)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert _switch_entity_id(hass, f"{RESIDENCE_ID}-{DEVICE_ID}-switch") is None


async def test_midrun_502_marks_unavailable_then_recovers(hass, api_behavior) -> None:
    """A 502 after startup makes entities unavailable until the next good poll."""
    entry = _entry()
    assert await _setup(hass, entry, api_behavior)
    entity_id = _switch_entity_id(hass, f"{RESIDENCE_ID}-{DEVICE_ID}-switch")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    api_behavior.update_error = LevitonException(502, "BadGateway", "bad gateway")
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    api_behavior.update_error = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


async def test_refreshed_token_is_saved_on_the_entry(hass, api_behavior) -> None:
    """A bearer refreshed during update is written back and the 2FA code is dropped."""
    api_behavior.new_authorization = NEW_TOKEN
    entry = _entry()

    assert await _setup(hass, entry, api_behavior)
    assert entry.data[CONF_TOKEN] == NEW_TOKEN
    assert CONF_CODE not in entry.data
    assert entry.data["login_response"]["id"] == NEW_TOKEN
    assert "code" not in entry.data["login_response"]


async def test_remove_config_entry_device_matches_account(hass, api_behavior) -> None:
    """Stale devices can be deleted. Devices the account still reports cannot."""
    entry = _entry()
    assert await _setup(hass, entry, api_behavior)
    device_registry = dr.async_get(hass)

    reported = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(DEVICE_ID)), entry.entry_id
    )
    assert reported is not None
    assert not await async_remove_config_entry_device(hass, entry, reported)

    residence = device_registry.async_get_device_by_identifier(
        (DOMAIN, str(RESIDENCE_ID)), entry.entry_id
    )
    assert residence is not None
    assert not await async_remove_config_entry_device(hass, entry, residence)

    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "999")},
        name="Removed switch",
    )
    assert await async_remove_config_entry_device(hass, entry, stale)


async def test_diagnostics_contain_no_secrets(hass, api_behavior) -> None:
    """Diagnostics redact credentials, tokens, serials, MACs, and account ids."""
    entry = _entry()
    assert await _setup(hass, entry, api_behavior)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    blob = str(diagnostics)
    for secret in (
        EMAIL,
        PASSWORD,
        TOKEN,
        CODE,
        USER_ID,
        RESIDENCE_ACCOUNT_ID,
        SERIAL,
        MAC,
    ):
        assert secret not in blob
    assert "**REDACTED**" in blob


async def test_migration_does_not_log_secrets(hass, api_behavior, caplog) -> None:
    """Migrating a v1 entry must not debug-log the stored secrets."""
    caplog.set_level(logging.DEBUG)
    entry = _entry(version=1)

    with caplog.at_level(logging.DEBUG):
        assert await _setup(hass, entry, api_behavior)

    assert entry.version == 2
    logged = integration_log_text(caplog)
    for secret in (EMAIL, PASSWORD, TOKEN, CODE, USER_ID, SERIAL, MAC):
        assert secret not in logged


async def test_options_flow_opens(hass, api_behavior) -> None:
    """The options flow shows the residence step without a TypeError."""
    entry = _entry()
    assert await _setup(hass, entry, api_behavior)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "residences"


async def test_existing_unique_ids_stay_when_mac_changes(hass, api_behavior) -> None:
    """MAC-based unique ids already in the registry are reused.

    A changed MAC and a missing MAC both keep the previous unique id. A device
    that has no registry entry yet gets the stable residence-device id, even
    when the cloud sent a MAC.
    """
    changed_id = 101
    missing_id = 303
    fresh_id = 202
    fresh_mac = "99:88:77:66:55:44"
    api_behavior.devices = [
        device_payload(changed_id, mac="11:22:33:44:55:66", name="Changed"),
        device_payload(missing_id, mac=None, name="Missing"),
        device_payload(fresh_id, mac=fresh_mac, name="Fresh"),
    ]
    entry = _entry(data=entry_data(**{CONF_DEVICES: [changed_id, missing_id, fresh_id]}))
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for device_id, legacy in (
        (changed_id, f"{LEGACY_MAC}-switch"),
        (missing_id, f"{MISSING_MAC_LEGACY}-switch"),
    ):
        device = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, str(device_id))},
            name=f"Device {device_id}",
        )
        entity_registry.async_get_or_create(
            "switch",
            DOMAIN,
            legacy,
            config_entry=entry,
            device_id=device.id,
        )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    unique_ids = {
        registry_entry.unique_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if registry_entry.domain == "switch" and registry_entry.unique_id.endswith("-switch")
    }
    assert f"{LEGACY_MAC}-switch" in unique_ids
    assert f"{MISSING_MAC_LEGACY}-switch" in unique_ids
    assert f"{RESIDENCE_ID}-{changed_id}-switch" not in unique_ids
    assert f"{RESIDENCE_ID}-{missing_id}-switch" not in unique_ids
    assert f"{RESIDENCE_ID}-{fresh_id}-switch" in unique_ids
    assert f"{fresh_mac}-switch" not in unique_ids
