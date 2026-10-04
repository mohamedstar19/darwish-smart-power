"""Sign in with a Darwish Smart Power account (or an invite code)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:                                     # Home Assistant before 2024.4
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .api import DarwishApi, DarwishAuthError, DarwishError
from .const import CONF_TOKEN, DEFAULT_URL, DOMAIN

CONF_CODE = "code"


class DarwishConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two ways in: a customer account (mobile/e-mail + password) or an invite code."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["account", "code"])

    async def _finish(self, url: str, token: str, title: str) -> ConfigFlowResult:
        api = DarwishApi(async_get_clientsession(self.hass), url, token)
        state = await api.state()                       # proves the token works
        me = state.get("me") or {}
        unique = f"{url}|{me.get('login') or me.get('name') or me.get('role', '')}"
        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(self._reauth_entry, data={CONF_URL: url, CONF_TOKEN: token})
            await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            return self.async_abort(reason="reauth_successful")
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=me.get("name") or title, data={CONF_URL: url, CONF_TOKEN: token})

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            api = DarwishApi(async_get_clientsession(self.hass), url)
            try:
                token = await api.sign_in(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                return await self._finish(url, token, user_input[CONF_USERNAME])
            except DarwishAuthError:
                errors["base"] = "invalid_auth"
            except DarwishError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema({
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            try:
                return await self._finish(url, user_input[CONF_CODE].strip(), "Darwish Smart Power")
            except DarwishAuthError:
                errors["base"] = "invalid_auth"
            except DarwishError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({
                vol.Required(CONF_URL, default=DEFAULT_URL): str,
                vol.Required(CONF_CODE): str,
            }),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The server stopped accepting the sign-in (signed out, password changed...)."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_user()
