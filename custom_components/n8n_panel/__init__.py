"""n8n Panel — the n8n workflow editor, as a native panel inside Home
Assistant (no add-on container, no ingress proxy).

Home Assistant itself connects to the n8n instance on the LAN (server
side) and reverse-proxies its SPA through HA, rewriting n8n's responses
so every URL runs under the HA-side prefix (``/api/n8n_panel``). The
browser only ever talks to Home Assistant, so there is no mixed content
and no CORS problem — and because the rewriting is done here in code
(see proxy.py), the three failure modes that kept n8n from loading
through hass_ingress do not apply:

1. ``window.BASE_PATH`` is set to the prefix WITH a trailing slash
   (n8n concatenates BASE_PATH + 'rest' with no separator).
2. Vite lazy-chunk paths are rewritten WITHOUT a leading slash, because
   n8n's preload helper (``$re = e => '/' + e``) prepends one itself —
   an absolute path would become protocol-relative ``//api/...`` and the
   browser would treat "api" as a host.
3. The session-cookie Path is scoped under the prefix and never
   double-prefixed, so the browser sends it back on every request.

The user logs in with their n8n credentials inside the panel; no n8n
config change (no N8N_PATH, no restart) is needed.
"""

from __future__ import annotations

import logging

from homeassistant.components import frontend, panel_custom
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, HA_ASSET_PREFIX, VERSION
from .views import N8nPanelAssetView, N8nProxyRootView, N8nProxyView, N8nSessionView

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "n8n"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up n8n Panel from a config entry."""
    options = {**entry.data, **entry.options}
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].update(options)

    hass.http.register_view(N8nPanelAssetView())
    hass.http.register_view(N8nSessionView(hass))
    hass.http.register_view(N8nProxyRootView(hass))
    hass.http.register_view(N8nProxyView(hass))

    # Sidebar panel as a custom element: it opens a full-size iframe to
    # the proxy root. A plain iframe panel would work too, but the
    # custom element keeps the panel module versioned (cache-busted on
    # update) the same way the Hermes Chat and HA Terminal panels do.
    await panel_custom.async_register_panel(
        hass,
        PANEL_URL_PATH,
        "n8n-panel",
        sidebar_title="n8n",
        sidebar_icon="mdi:workflow",
        module_url=f"{HA_ASSET_PREFIX}/panel.js?v={VERSION}",
        require_admin=True,
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _LOGGER.info("n8n Panel ready: panel at /%s", PANEL_URL_PATH)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload n8n Panel."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH)
    hass.data.pop(DOMAIN, None)
    return True
