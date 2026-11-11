# n8n Panel for Home Assistant

The n8n workflow editor, as a native panel inside Home Assistant — no
add-on container, no ingress proxy.

Home Assistant itself connects to the n8n instance on the LAN (server
side) and reverse-proxies its SPA through HA, rewriting n8n's responses
so every URL runs under the HA-side prefix (`/api/n8n_panel`). The
browser only ever talks to Home Assistant, so there is no mixed content
and no CORS problem.

## Why not hass_ingress?

n8n kept failing to load through hass_ingress, one layer at a time:

1. The API went to `/api/ingress/n8nrest/settings` — n8n concatenates
   `BASE_PATH + 'rest'` with no separator, so the base path needs a
   trailing slash.
2. The lazy chunks (SigninView, AuthView) 404'd at `/assets/...` —
   Vite's `__vite__mapDeps` preload resolves relative `assets/` paths
   to the HA root.
3. Fixing (2) with absolute chunk paths made them protocol-relative
   `//api/...` — n8n's preload helper is `$re = e => '/' + e`, it
   prepends one slash itself, so an absolute path becomes
   `//api/ingress/...` and the browser treats `api` as a host.

Each fix needed a new regex rule and a new live test, and the rules
interact (a rule that fixes pages can break chunk loading). Doing the
rewriting in code, in this integration, handles all three correctly in
one place — and the rewriting is tested against n8n's real responses
before it ships.

## Install

1. In HACS, add this repository as a custom repository (Integration),
   then install **n8n Panel** and restart Home Assistant.
2. Go to Settings → Devices & Services → Add Integration → **n8n Panel**.
3. Enter the n8n host (default `192.168.2.10`) and port (default
   `5678`). The setup tests the connection against n8n's `/healthz`
   endpoint before creating the entry.
4. Open the **n8n** item in the sidebar and log in with your n8n
   credentials.

No n8n config change is needed (no `N8N_PATH`, no restart of n8n).
The n8n instance is reached on the LAN by Home Assistant; the browser
never talks to it directly.

## How it works

- `custom_components/n8n_panel/proxy.py` — the reverse proxy and all
  rewriting: `window.BASE_PATH` (with trailing slash), Vite chunk
  paths (no leading slash, for `$re`), HTML asset paths, CSS `url()`,
  `Location` redirects, and the session-cookie `Path` (scoped under
  the prefix, never double-prefixed).
- `custom_components/n8n_panel/views.py` — the proxy catch-all
  (`/api/n8n_panel/{path}`), including WebSocket relay for n8n's
  live-push channel (`/rest/push`), plus the panel element asset.
- `custom_components/n8n_panel/frontend/panel.js` — the sidebar panel
  element: a full-size iframe to the proxy root.
- The proxy routes require a logged-in HA user; n8n's own login
  happens inside the panel.

## Version log

- **0.1.4** — fix the 500 for real: HA's view dispatcher calls the
  handler as `handler(request, **request.match_info)`, so the
  `{path:.*}` capture from the proxy route arrives as a `path`
  keyword argument — but `_handle` did not accept it, so every
  proxied request died with `TypeError: _handle() got an
  unexpected keyword argument 'path'` (read from the HA log after
  the restart; first occurrence 11:01:32 PT). The bug was latent
  from v0.1.0 and only surfaced once the proxy stopped requiring
  a Bearer header (v0.1.2), because before that HA's auth
  middleware rejected the iframe request before `_handle` was
  ever called. `_handle` now takes `path` and uses it as the
  upstream subpath. (The 0.1.3 content_type fix was also real —
  it was the next 500 behind this one.)
- **0.1.3** — fix the 500 on the panel: the proxy built its response
  with both the Content-Type header (forwarded from n8n) and a
  `content_type=` parameter, and aiohttp raises ValueError ("passing
  both Content-Type header and content_type ... is forbidden") for
  that — the unhandled exception became the 500 "Server got itself
  in trouble" page. The response now carries n8n's Content-Type
  header as-is, with no `content_type=` parameter.
- **0.1.2** — fix the 401 on the panel: an iframe load is a plain
  browser navigation and cannot send a Bearer header, so
  `requires_auth=True` on the proxy rejected the iframe even for a
  logged-in admin. The panel now calls a session endpoint with its HA
  token (`fetchWithAuth`), the integration sets an admin-only,
  HttpOnly session cookie scoped to the proxy path, and the proxy
  validates that cookie on every request (sliding 8 h expiry). The
  proxy answers 401 itself without a live session.
- **0.1.1** — fix: the proxy view no longer defines an `options`
  handler. Home Assistant's CORS setup adds its own preflight
  OPTIONS handler to every route, and defining one made setup fail
  with "already has OPTIONS handler". n8n's SPA talks to HA
  same-origin, so it never sends a CORS preflight anyway.
- **0.1.0** — first version: reverse proxy + sidebar panel, with the
  three ingress failure modes handled in code (BASE_PATH trailing
  slash, `$re` chunk paths without a leading slash, cookie Path
  scoping). Validated against n8n 2.42.7's real responses.
