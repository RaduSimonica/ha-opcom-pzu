"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import PzuCoordinator


class PzuEntity(CoordinatorEntity[PzuCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: PzuCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=NAME,
            manufacturer="OPCOM",
            model="PZU day-ahead market",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.opcom.ro/grafice-ip-raportPIP-si-volumTranzactionat/ro",
        )
