"""Config flow for n8n Panel."""

from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector

from .const import (
    CONF_HOST,
    CONF_PORT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
)


def _schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, DEFAULT_HOST)
            ): TextSelector(),
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): int,
        }
    )


async def _test_connection(host: str, port: int) -> tuple[bool, str]:
    """Check that n8n answers on the given host and port.

    Uses n8n's /healthz endpoint (no auth required) so a bad host or
    a down n8n is reported before the entry is created.
    """
    url = f"http://{host}:{port}/healthz"
    try:
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    return True, ""
                return False, f"n8n answered with status {resp.status}"
    except (aiohttp.ClientError, TimeoutError) as err:
        return False, str(err)


class N8nPanelConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up n8n Panel."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_HOST, "")).strip()
            try:
                port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            except (TypeError, ValueError):
                port = 0
            if not host or port < 1 or port > 65535:
                errors["base"] = "invalid_input"
            else:
                ok, message = await _test_connection(host, port)
                if ok:
                    return self.async_create_entry(title="n8n Panel", data=user_input)
                errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=_schema({}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return N8nPanelOptionsFlow(config_entry)


class N8nPanelOptionsFlow(OptionsFlow):
    """Change n8n Panel options."""

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(defaults))
