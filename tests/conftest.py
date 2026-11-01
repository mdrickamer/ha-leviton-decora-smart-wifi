"""Test fixtures for the Leviton Decora Smart Wi-Fi integration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture
def hass_config_dir(tmp_path: Path) -> str:
    """Point Home Assistant at a config dir that contains this integration."""
    destination = tmp_path / "custom_components" / "leviton_decora_smart_wifi"
    destination.parent.mkdir(parents=True)
    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "leviton_decora_smart_wifi"
    )
    destination.symlink_to(source)
    return str(tmp_path)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the custom component instead of a core integration."""


@pytest.fixture(autouse=True)
def mock_leviton_websocket():
    """Do not open a socket to my.leviton.com from the websocket client."""
    with (
        patch(
            "custom_components.leviton_decora_smart_wifi.LevitonWebSocket.start",
            lambda self: None,
        ),
        patch(
            "custom_components.leviton_decora_smart_wifi.LevitonWebSocket.stop",
            new=AsyncMock(),
        ),
    ):
        yield


@pytest.fixture
def api_behavior():
    """Replace LevitonAPI login/update with an in-memory fake."""
    from tests.common import ApiBehavior

    behavior = ApiBehavior()

    def update(api, target_residences=None):
        return behavior.update(api, target_residences)

    def login(api, email, password, code=None):
        return behavior.login(api, email, password, code)

    with (
        patch(
            "custom_components.leviton_decora_smart_wifi.api.LevitonAPI.update",
            update,
        ),
        patch(
            "custom_components.leviton_decora_smart_wifi.api.LevitonAPI.login",
            login,
        ),
    ):
        yield behavior
