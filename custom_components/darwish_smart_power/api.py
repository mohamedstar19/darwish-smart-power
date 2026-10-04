"""Small client for the Darwish Smart Power server (smartpower.py) JSON API."""

from __future__ import annotations

from typing import Any

import aiohttp


class DarwishError(Exception):
    """The server could not be reached or answered with an error."""


class DarwishAuthError(DarwishError):
    """Wrong account details, or the sign-in is no longer accepted."""


class DarwishLockedError(DarwishError):
    """The strip is locked with a PIN in the app."""


class DarwishApi:
    """Talks to one Darwish Smart Power server with a sign-in token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str = "") -> None:
        self._session = session
        self.url = url.rstrip("/")
        self.token = token

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["X-Token"] = self.token
        try:
            async with self._session.request(
                method, f"{self.url}/{path}", json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=25)
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 401:
                    raise DarwishAuthError(data.get("error", "not signed in") if isinstance(data, dict) else "not signed in")
                if resp.status == 403 and isinstance(data, dict) and data.get("locked"):
                    raise DarwishLockedError(data.get("error", "locked"))
                if resp.status >= 400:
                    raise DarwishError(data.get("error", f"HTTP {resp.status}") if isinstance(data, dict) else f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise DarwishError(str(err)) from err

    async def sign_in(self, login: str, password: str) -> str:
        """Customer account sign-in; returns a token for this Home Assistant."""
        data = await self._call("POST", "api/login", {"login": login, "password": password})
        self.token = str(data.get("token", ""))
        if not self.token:
            raise DarwishAuthError("no token")
        return self.token

    async def state(self) -> dict[str, Any]:
        """Strips with outlets, readings and the signed-in person ("me")."""
        return await self._call("GET", "api/state")

    async def switch(self, strip_id: str, outlet: int, on: bool) -> None:
        """Outlet 1-4, or 0 for every outlet of the strip."""
        await self._call("POST", "api/switch", {"strip": strip_id, "outlets": [outlet], "on": on})
