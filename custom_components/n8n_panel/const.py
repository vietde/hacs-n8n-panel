"""Constants for the n8n Panel integration."""

DOMAIN = "n8n_panel"

CONF_HOST = "host"
CONF_PORT = "port"

DEFAULT_HOST = "192.168.2.10"
DEFAULT_PORT = 5678

# Integration version (mirrors manifest.json). Used to bust the browser
# cache on the panel module URL after an update.
VERSION = "0.1.0"

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
