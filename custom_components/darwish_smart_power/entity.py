"""Base entity: one device per strip."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DarwishCoordinator


class DarwishEntity(CoordinatorEntity[DarwishCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DarwishCoordinator, strip_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.strip_id = strip_id
        self._attr_unique_id = f"{strip_id}_{key}"
        strip = self.strip
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, strip_id)},
            name=strip.get("name") or f"Strip {strip_id[-6:]}",
            manufacturer=MANUFACTURER,
            model=strip.get("model") or "MTTL-W01",
            sw_version=strip.get("fw") or None,
            suggested_area=strip.get("room") or None,
        )

    @property
    def strip(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.strip_id, {})

    def outlet(self, index: int) -> dict[str, Any]:
        return next((o for o in self.strip.get("outlets", []) if o.get("index") == index), {})

    @property
    def available(self) -> bool:
        return super().available and bool(self.strip.get("online"))
