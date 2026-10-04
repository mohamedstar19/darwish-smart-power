"""Polls the server for every strip on the account."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DarwishApi, DarwishAuthError, DarwishError
from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class DarwishCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """data: strip id -> strip as the server describes it."""

    def __init__(self, hass: HomeAssistant, api: DarwishApi) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS))
        self.api = api

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            state = await self.api.state()
        except DarwishAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DarwishError as err:
            raise UpdateFailed(str(err)) from err
        return {strip["id"]: strip for strip in state.get("strips", [])}
