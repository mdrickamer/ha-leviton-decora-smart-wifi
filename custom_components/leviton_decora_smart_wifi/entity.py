"""Base class for Leviton Decora Smart Wi-Fi entities."""

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import LevitonDataUpdateCoordinator
from .api.activity import Activity as LevitonActivity
from .api.button import Button as LevitonButton
from .api.device import Device as LevitonDevice
from .api.firmware import Firmware as LevitonFirmware
from .api.residence import Residence as LevitonResidence
from .api.room import Room as LevitonRoom
from .api.scene import Scene as LevitonScene
from .api.schedule import Schedule as LevitonSchedule
from .const import (
    CONFIGURATION_URL,
    DEVICE_INFO_MANUFACTURER,
    DEVICE_INFO_MODEL_RESIDENCE,
    DOMAIN,
    UPDATE_NOTIFICATION,
)
from .util import generate_device_identifier


class LevitonEntity(CoordinatorEntity[LevitonDataUpdateCoordinator]):
    """Representation of a Leviton entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LevitonDataUpdateCoordinator,
        config_entry: ConfigEntry,
        residence_id: int,
        activity_id: int | None = None,
        schedule_id: int | None = None,
        room_id: int | None = None,
        scene_id: int | None = None,
        device_id: int | None = None,
        button_id: int | None = None,
        entity_description: EntityDescription | None = None,
    ) -> None:
        """Initialize the device."""
        super().__init__(coordinator)
        self.config_entry_id = config_entry.entry_id
        self.residence_id = residence_id
        self.activity_id = activity_id
        self.schedule_id = schedule_id
        self.room_id = room_id
        self.scene_id = scene_id
        self.device_id = device_id
        self.button_id = button_id
        if entity_description:
            self.entity_description = entity_description
        self._attr_unique_id = self._generate_unique_id()

    @property
    def residence(self) -> LevitonResidence | None:
        """Return a LevitonResidence object."""
        residences = {
            residence.id: residence for residence in self.coordinator.data.residences
        }
        return residences.get(self.residence_id)

    @property
    def firmware(self) -> LevitonFirmware | None:
        """Return a LevitonFirmware object."""
        if self.device and self.device.model:
            return self.coordinator.data.firmware.get(self.device.model)
        return None

    @property
    def activity(self) -> LevitonActivity | None:
        """Return a LevitonActivity object."""
        if self.residence:
            activities = {
                activity.id: activity for activity in self.residence.activities
            }
            return activities.get(self.activity_id)
        return None

    @property
    def schedule(self) -> LevitonSchedule | None:
        """Return a LevitonSchedule object."""
        if self.residence:
            schedules = {schedule.id: schedule for schedule in self.residence.schedules}
            return schedules.get(self.schedule_id)
        return None

    @property
    def room(self) -> LevitonRoom | None:
        """Return a LevitonRoom object."""
        if self.residence:
            rooms = {room.id: room for room in self.residence.rooms}
            return rooms.get(self.room_id)
        return None

    @property
    def scene(self) -> LevitonScene | None:
        """Return a LevitonScene object."""
        if self.room:
            scenes = {scene.id: scene for scene in self.room.scenes}
            return scenes.get(self.scene_id)
        return None

    @property
    def device(self) -> LevitonDevice | None:
        """Return a LevitonDevice object."""
        if self.residence:
            devices = {device.id: device for device in self.residence.devices}
            return devices.get(self.device_id)
        return None

    @property
    def button(self) -> LevitonButton | None:
        """Return a LevitonButton object."""
        if self.device:
            buttons = {button.id: button for button in self.device.buttons}
            return buttons.get(self.button_id)
        return None

    @property
    def target(
        self,
    ) -> (
        LevitonResidence
        | LevitonActivity
        | LevitonSchedule
        | LevitonRoom
        | LevitonScene
        | LevitonDevice
        | LevitonButton
        | None
    ):
        """Return the target object."""
        if self.button:
            return self.button
        if self.device:
            return self.device
        if self.scene:
            return self.scene
        if self.room:
            return self.room
        if self.schedule:
            return self.schedule
        if self.activity:
            return self.activity
        return self.residence

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        available = super().available
        if self.device:
            return all(
                [
                    available,
                    self.device.is_connected,
                ]
            )
        return available

    @property
    def device_info(self) -> dr.DeviceInfo | None:
        """Return device specific attributes.

        Implemented by platform classes.
        """
        if self.residence and self.residence.id:
            if self.device and self.device.id:
                return dr.DeviceInfo(
                    configuration_url=CONFIGURATION_URL,
                    identifiers={generate_device_identifier(self.device.id)},
                    manufacturer=self.device.manufacturer,
                    model=self.device.model,
                    name=self.device.name,
                    serial_number=self.device.serial,
                    suggested_area=self.device.room_name,
                    sw_version=self.device.version,
                    via_device_id=dr.async_get_device_id_by_identifier(
                        self.hass,
                        generate_device_identifier(self.residence.id),
                        config_entry_id=self.config_entry_id,
                    ),
                )
            return dr.DeviceInfo(
                configuration_url=CONFIGURATION_URL,
                entry_type=dr.DeviceEntryType.SERVICE,
                identifiers={generate_device_identifier(self.residence.id)},
                manufacturer=DEVICE_INFO_MANUFACTURER,
                model=DEVICE_INFO_MODEL_RESIDENCE,
                name=self.residence.name,
            )
        return None

    @property
    def name(self) -> str | None:
        """Return the name of the entity."""
        if self.activity:
            return f"{self.activity.name} Activity"
        if self.schedule:
            return f"{self.schedule.name} Schedule"
        if self.scene:
            return f"{self.scene.name} Scene"
        if self.button:
            return f"{self.button.text}"
        return (
            self.entity_description.name
            if self.entity_description and isinstance(self.entity_description.name, str)
            else None
        )

    def _generate_unique_id(self) -> str | int | None:
        """Generate a unique ID.

        Device entities used to be ``{mac}-{key}`` when the cloud sent a MAC
        and ``{residence_id}-{device_id}-{key}`` when it did not. Either value
        changed when the MAC appeared, disappeared, or was replaced, and Home
        Assistant then created a second entity.

        Entities already in the registry keep the unique id they have. New
        device entities use ``{residence_id}-{device_id}-{key}`` and do not
        include the MAC. Residence, activity, schedule, and scene ids are
        unchanged.
        """
        if self.device_id is not None:
            return self._device_unique_id()

        unique_id: str | int | None = self.residence_id
        if self.activity_id:
            return f"{unique_id}-{self.activity_id}"
        if self.schedule_id:
            return f"{unique_id}-{self.schedule_id}"
        if self.room_id and self.scene_id:
            return f"{unique_id}-{self.room_id}-{self.scene_id}"
        if self.button_id:
            return f"{unique_id}-{self.button_id}"
        if self.entity_description and (key := self.entity_description.key):
            return f"{unique_id}-{key}"
        return unique_id

    def _device_unique_id(self) -> str:
        """Return a stable unique id for a device-scoped entity."""
        suffix = self._device_unique_suffix()
        stable = self._format_device_unique_id(
            f"{self.residence_id}-{self.device_id}", suffix
        )
        existing = self._existing_device_unique_id(stable, suffix)
        if existing is not None:
            return existing
        return stable

    def _device_unique_suffix(self) -> str:
        """Return the part of the unique id that distinguishes the entity."""
        if self.button_id is not None:
            return str(self.button_id)
        if self.entity_description and self.entity_description.key:
            return str(self.entity_description.key)
        return ""

    @staticmethod
    def _format_device_unique_id(base: str, suffix: str) -> str:
        """Join a device base id and an entity suffix."""
        if suffix:
            return f"{base}-{suffix}"
        return base

    def _existing_device_unique_id(self, stable: str, suffix: str) -> str | None:
        """Return an already-registered unique id for this entity, if any."""
        hass = getattr(self.coordinator, "hass", None)
        if hass is None:
            return None

        ent_reg = er.async_get(hass)
        mac = self.device.mac if self.device else None
        if mac:
            legacy = self._format_device_unique_id(mac, suffix)
            if self._registry_has_unique_id(ent_reg, legacy):
                return legacy
        if self._registry_has_unique_id(ent_reg, stable):
            return stable

        dev_reg = dr.async_get(hass)
        device_entry = dev_reg.async_get_device_by_identifier(
            generate_device_identifier(self.device_id),
            self.config_entry_id,
        )
        if device_entry is None:
            return None

        matches = [
            entry
            for entry in er.async_entries_for_device(
                ent_reg, device_entry.id, include_disabled_entities=True
            )
            if entry.platform == DOMAIN
            and entry.config_entry_id == self.config_entry_id
            and self._unique_id_has_suffix(entry.unique_id, suffix)
        ]
        if not matches:
            return None
        return self._prefer_existing_unique_id(matches, stable)

    def _registry_has_unique_id(
        self, ent_reg: er.EntityRegistry, unique_id: str
    ) -> bool:
        """Return True if this config entry already has the unique id."""
        for entry in ent_reg.entities.values():
            if (
                entry.platform == DOMAIN
                and entry.config_entry_id == self.config_entry_id
                and entry.unique_id == unique_id
            ):
                return True
        return False

    @staticmethod
    def _unique_id_has_suffix(unique_id: str, suffix: str) -> bool:
        """Return True if unique_id belongs to this entity's suffix."""
        if not suffix:
            return "-" not in unique_id
        return unique_id == suffix or unique_id.endswith(f"-{suffix}")

    @staticmethod
    def _prefer_existing_unique_id(matches: list[er.RegistryEntry], stable: str) -> str:
        """Pick the unique id an upgrade must keep using.

        A MAC-based id is preferred over the residence-device fallback when
        both already exist, so the original entity stays in place.
        """
        enabled = [entry for entry in matches if entry.disabled_by is None]
        pool = enabled or matches
        legacy = [entry for entry in pool if entry.unique_id != stable]
        if len(legacy) == 1:
            return legacy[0].unique_id
        for entry in pool:
            if entry.unique_id == stable:
                return stable
        return sorted(pool, key=lambda entry: entry.unique_id)[0].unique_id

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass.

        To be extended by integrations.
        """
        await super().async_added_to_hass()
        if self.device and self.device.id:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"{UPDATE_NOTIFICATION}_{self.device.id}",
                    self.handle_notification,
                )
            )

    @callback
    def handle_notification(self, notification: dict[str, Any]) -> None:
        """Handle notification."""
        self.async_write_ha_state()
