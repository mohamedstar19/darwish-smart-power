"""One switch per outlet, plus one for the whole strip."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import DarwishError, DarwishLockedError
from .const import DOMAIN
from .coordinator import DarwishCoordinator
from .entity import DarwishEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: DarwishCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def add_new() -> None:
        new: list[SwitchEntity] = []
        for strip_id, strip in coordinator.data.items():
            for outlet in [0] + [o["index"] for o in strip.get("outlets", [])]:
                key = f"{strip_id}_{outlet}"
                if key not in known:
                    known.add(key)
                    new.append(DarwishSwitch(coordinator, strip_id, outlet))
        if new:
            add(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))   # strips added later show up too


class DarwishSwitch(DarwishEntity, SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: DarwishCoordinator, strip_id: str, outlet: int) -> None:
        super().__init__(coordinator, strip_id, f"switch_{outlet}")
        self.index = outlet
        if outlet == 0:
            self._attr_translation_key = "all_outlets"

    @property
    def name(self) -> str | None:
        if self.index == 0:
            return super().name                     # "All outlets", from the translations
        return self.outlet(self.index).get("name") or f"Outlet {self.index}"

    @property
    def icon(self) -> str | None:
        return "mdi:power-socket-eu" if self.index else "mdi:power-plug-outline"

    @property
    def is_on(self) -> bool:
        if self.index == 0:
            return any(o.get("on") for o in self.strip.get("outlets", []))
        return bool(self.outlet(self.index).get("on"))

    async def _switch(self, on: bool) -> None:
        try:
            await self.coordinator.api.switch(self.strip_id, self.index, on)
        except DarwishLockedError as err:
            raise HomeAssistantError("This strip is locked with a PIN in the Darwish Smart Power app") from err
        except DarwishError as err:
            raise HomeAssistantError(f"Could not switch: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._switch(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._switch(False)
