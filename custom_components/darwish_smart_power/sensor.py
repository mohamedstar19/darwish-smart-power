"""Readings: power, energy (works with the Energy dashboard), temperature, voltage, current, Wi-Fi."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DarwishCoordinator
from .entity import DarwishEntity


@dataclass(frozen=True, kw_only=True)
class DarwishSensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any] = lambda _: None


STRIP_SENSORS = (
    DarwishSensorDescription(key="power", translation_key="power", device_class=SensorDeviceClass.POWER,
                             state_class=SensorStateClass.MEASUREMENT, native_unit_of_measurement=UnitOfPower.WATT,
                             value=lambda s: s.get("watts")),
    DarwishSensorDescription(key="energy", translation_key="energy", device_class=SensorDeviceClass.ENERGY,
                             state_class=SensorStateClass.TOTAL_INCREASING,
                             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value=lambda s: s.get("kwh")),
    DarwishSensorDescription(key="voltage", translation_key="voltage", device_class=SensorDeviceClass.VOLTAGE,
                             state_class=SensorStateClass.MEASUREMENT,
                             native_unit_of_measurement=UnitOfElectricPotential.VOLT, value=lambda s: s.get("volts")),
    DarwishSensorDescription(key="current", translation_key="current", device_class=SensorDeviceClass.CURRENT,
                             state_class=SensorStateClass.MEASUREMENT,
                             native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, value=lambda s: s.get("amps")),
    DarwishSensorDescription(key="rssi", translation_key="rssi", device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                             state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
                             native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                             entity_registry_enabled_default=False, value=lambda s: s.get("rssi")),
)

OUTLET_SENSORS = (
    DarwishSensorDescription(key="power", device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
                             native_unit_of_measurement=UnitOfPower.WATT, value=lambda o: o.get("watts")),
    DarwishSensorDescription(key="energy", device_class=SensorDeviceClass.ENERGY,
                             state_class=SensorStateClass.TOTAL_INCREASING,
                             native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, value=lambda o: o.get("kwh")),
    DarwishSensorDescription(key="temperature", device_class=SensorDeviceClass.TEMPERATURE,
                             state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC,
                             native_unit_of_measurement=UnitOfTemperature.CELSIUS, value=lambda o: o.get("temp_c")),
)



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    coordinator: DarwishCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    def add_new() -> None:
        new: list[SensorEntity] = []
        for strip_id, strip in coordinator.data.items():
            for desc in STRIP_SENSORS:
                key = f"{strip_id}_{desc.key}"
                if key not in known:
                    known.add(key)
                    new.append(StripSensor(coordinator, strip_id, desc))
            for outlet in strip.get("outlets", []):
                for desc in OUTLET_SENSORS:
                    key = f"{strip_id}_{outlet['index']}_{desc.key}"
                    if key not in known:
                        known.add(key)
                        new.append(OutletSensor(coordinator, strip_id, outlet["index"], desc))
        if new:
            add(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class StripSensor(DarwishEntity, SensorEntity):
    entity_description: DarwishSensorDescription

    def __init__(self, coordinator: DarwishCoordinator, strip_id: str, desc: DarwishSensorDescription) -> None:
        super().__init__(coordinator, strip_id, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.strip)


class OutletSensor(DarwishEntity, SensorEntity):
    entity_description: DarwishSensorDescription

    def __init__(self, coordinator: DarwishCoordinator, strip_id: str, index: int, desc: DarwishSensorDescription) -> None:
        super().__init__(coordinator, strip_id, f"{index}_{desc.key}")
        self.entity_description = desc
        self.index = index

    @property
    def name(self) -> str:
        outlet = self.outlet(self.index).get("name") or f"Outlet {self.index}"
        return f"{outlet} {self.entity_description.key}"

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.outlet(self.index))
