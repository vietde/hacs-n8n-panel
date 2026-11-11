"""Constants for the n8n Panel integration."""

DOMAIN = "n8n_panel"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_HOST = "192.168.2.10"
DEFAULT_PORT = 5678

# Integration version (mirrors manifest.json). Used to bust the browser
# cache on the panel module URL after an update.
VERSION = "0.1.4"

# Panel session cookie: the browser gets it from the session endpoint
# (after HA validates the admin token) and sends it on every proxied
# request, because an iframe load cannot send a Bearer header.
SESSION_COOKIE = "n8n_panel_session"
# Server-side session lifetime (sliding: extended on each use, so it
# stays alive while the panel is in use and ends after 8 h idle).
SESSION_TTL = 8 * 3600
# Cookie lifetime in the browser (longer than the server TTL, so the
# cookie is still there when the server-side session slides forward).
SESSION_COOKIE_MAX_AGE = 24 * 3600

# The HA-side prefix under which n8n is served (the proxy root).
PROXY_PREFIX = "/api/n8n_panel"
# The same prefix without the leading slash. Vite's preload helper
# ($re = e => '/' + e) prepends one slash itself, so chunk paths
# rewritten for it must NOT start with a slash — otherwise they become
# protocol-relative //api/... and the browser treats "api" as a host.
PROXY_PREFIX_NOSLASH = "api/n8n_panel"
# Prefix for the integration's own assets (the panel element). Kept
# separate from the proxied n8n paths so the proxy catch-all never
# swallows them.
HA_ASSET_PREFIX = "/api/n8n_panel/__ha__"
