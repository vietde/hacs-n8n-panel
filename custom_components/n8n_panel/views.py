"""HTTP views: the n8n panel asset, a session endpoint, and the
reverse proxy to n8n.

The sidebar panel is a custom element (panel.js) that opens a full-size
iframe to the proxy root (``/api/n8n_panel/``). The iframe loads n8n's
own SPA through the proxy, so the user gets the real n8n UI — sign-in,
workflows, executions — inside Home Assistant, and logs in with their
n8n credentials there.

Auth: an iframe load is a plain browser navigation — it cannot send an
``Authorization: Bearer`` header, so ``requires_auth=True`` on the
proxy rejects it with 401 even for a logged-in admin (that is what
v0.1.1 did). Instead the panel element first calls the session
endpoint with its HA token (``hass.fetchWithAuth`` sends the Bearer
header), the integration sets a session cookie, and the proxy
validates that cookie on every request — including the ones n8n's SPA
makes afterwards, which cannot carry a token of their own. The
session is admin-only, HttpOnly, scoped to the proxy path, and slides
its expiry forward on each use (8 h of inactivity ends it).

The proxy forwards every request to the n8n instance on the LAN and
rewrites the responses (see proxy.py) so all of n8n's URLs run under
the HA-side prefix. WebSocket upgrades (n8n's live-push channel at
``/rest/push``) are relayed frame-by-frame.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PORT,
    HA_ASSET_PREFIX,
    PROXY_PREFIX,
    SESSION_COOKIE,
    SESSION_COOKIE_MAX_AGE,
    SESSION_TTL,
)
from .proxy import (
    filter_request_headers,
    filter_response_headers,
    rewrite_body,
    rewrite_location,
    rewrite_set_cookie,
)

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent / "frontend"


def _n8n_base_url(hass: HomeAssistant) -> str | None:
    """The n8n instance's base URL, or None when not configured."""
    data = hass.data.get("n8n_panel") or {}
    host = str(data.get(CONF_HOST) or "").strip()
    try:
        port = int(data.get(CONF_PORT) or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or port < 1 or port > 65535:
        return None
    return f"http://{host}:{port}"


def _sessions(hass: HomeAssistant) -> dict[str, float]:
    """The live panel sessions: {token: expiry monotonic timestamp}."""
    data = hass.data.setdefault("n8n_panel", {})
    return data.setdefault("_sessions", {})


def _validate_session(hass: HomeAssistant, request: web.Request) -> bool:
    """True when the request carries a live panel session cookie.

    Expired sessions are pruned on the way through, and a live one has
    its expiry slid forward (8 h from now), so the session stays alive
    while the panel is in use and ends after 8 h idle.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    sessions = _sessions(hass)
    now = time.monotonic()
    expired = [t for t, exp in sessions.items() if exp <= now]
    for t in expired:
        sessions.pop(t, None)
    expiry = sessions.get(token)
    if expiry is None or expiry <= now:
        return False
    sessions[token] = now + SESSION_TTL
    return True


def _create_session(hass: HomeAssistant) -> str:
    """Mint a panel session token and store it with its expiry."""
    token = secrets.token_hex(32)
    _sessions(hass)[token] = time.monotonic() + SESSION_TTL
    return token


class N8nPanelAssetView(HomeAssistantView):
    """Serve the panel element (panel.js). Kept under a separate
    ``__ha__`` prefix so the proxy catch-all never swallows it."""

    url = HA_ASSET_PREFIX + "/{filename}"
    name = "n8n_panel:assets"
    requires_auth = False

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        path = FRONTEND_DIR / filename
        if path.suffix != ".js" or path.parent != FRONTEND_DIR or not path.is_file():
            return web.Response(status=404, text="not found")
        # Never cache the panel element: after a HACS update the browser
        # must fetch the new panel.js, not keep running the old one.
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "application/javascript",
                "Cache-Control": "no-cache, max-age=0",
            },
        )


class N8nSessionView(HomeAssistantView):
    """Create a panel session for the browser.

    The panel element calls this with its HA token (``fetchWithAuth``
    sends the Bearer header). Admin only. On success it sets the
    session cookie the proxy validates; the panel then opens the
    iframe. The cookie is HttpOnly and scoped to the proxy path, so
    only the proxied requests carry it.
    """

    url = HA_ASSET_PREFIX + "/session"
    name = "n8n_panel:session"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.StreamResponse:
        user = request.get("hass_user")
        if user is None or not user.is_admin:
            return self.json_message("admin required", status_code=403)
        token = _create_session(self.hass)
        response = self.json({"ok": True})
        response.set_cookie(
            SESSION_COOKIE,
            token,
            path=PROXY_PREFIX + "/",
            httponly=True,
            samesite="Lax",
            max_age=SESSION_COOKIE_MAX_AGE,
        )
        return response


class N8nProxyView(HomeAssistantView):
    """Reverse proxy to the n8n instance on the LAN.

    Catches every path under the proxy prefix and forwards it to n8n,
    rewriting the response so n8n's URLs run under the prefix. Also
    relays WebSocket upgrades (n8n's /rest/push live channel).

    ``requires_auth`` is False on purpose: an iframe load cannot send
    a Bearer header, so HA's own auth rejects it with 401. Access is
    gated by the panel session cookie instead (see N8nSessionView) —
    without a live session the proxy answers 401 itself.
    """

    url = PROXY_PREFIX + "/{path:.*}"
    name = "n8n_panel:proxy"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        if not _validate_session(self.hass, request):
            return web.Response(status=401, text="n8n Panel session required — reload the n8n panel.")
        base_url = _n8n_base_url(self.hass)
        if base_url is None:
            return web.Response(
                status=503,
                text="n8n Panel is not configured — open Settings → Devices & Services → n8n Panel and enter the n8n host and port.",
            )
        subpath = request.match_info.get("path", "")
        upstream = f"{base_url}/{subpath}"
        if request.query_string:
            upstream += f"?{request.query_string}"

        # WebSocket upgrade (n8n's /rest/push live channel): relay
        # frames between the browser and n8n instead of doing HTTP.
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._handle_websocket(request, upstream)

        headers = filter_request_headers(request.headers)
        body = await request.read()
        session = aiohttp.ClientSession(trust_env=True)
        try:
            async with session.request(
                request.method,
                upstream,
                headers=headers,
                data=body if body else None,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = await resp.read()
                out_headers = filter_response_headers(resp.headers)
                # Rewrites: Location (redirects), Set-Cookie (Path), body.
                if "Location" in out_headers:
                    out_headers["Location"] = rewrite_location(out_headers["Location"])
                cookies = resp.headers.getall("Set-Cookie", [])
                if cookies:
                    out_headers.pop("Set-Cookie", None)
                    rewritten = [rewrite_set_cookie(c) for c in cookies]
                    # aiohttp's CIMultiDict keeps duplicates; build the
                    # response with the rewritten cookies attached.
                    # NOTE: do NOT also pass content_type= here — the
                    # Content-Type header already rides in out_headers,
                    # and aiohttp raises ValueError ("passing both
                    # Content-Type header and content_type ... is
                    # forbidden") when both are given. That was the
                    # 500 on the panel (v0.1.2).
                    out_body = rewrite_body(raw, content_type)
                    response = web.Response(
                        status=resp.status,
                        body=out_body,
                        headers=out_headers,
                    )
                    for cookie in rewritten:
                        response.headers.add("Set-Cookie", cookie)
                    return response
                out_body = rewrite_body(raw, content_type)
                return web.Response(
                    status=resp.status,
                    body=out_body,
                    headers=out_headers,
                )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("n8n Panel: cannot reach %s (%s)", upstream, err)
            return web.Response(
                status=502,
                text=f"Cannot reach n8n at {base_url} ({err}). Make sure n8n is running.",
            )
        finally:
            await session.close()

    async def _handle_websocket(
        self, request: web.Request, upstream: str
    ) -> web.StreamResponse:
        """Relay a WebSocket between the browser and n8n."""
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        session = aiohttp.ClientSession(trust_env=True)
        try:
            upstream_ws = await session.ws_connect(
                upstream,
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k.lower() not in ("host", "upgrade", "connection", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions")
                },
            )
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("n8n Panel: cannot open websocket to %s (%s)", upstream, err)
            await ws.close(code=1011, message=b"upstream unreachable")
            await session.close()
            return ws

        async def _browser_to_n8n() -> None:
            try:
                async for msg in ws:
                    if msg.type == web.WSMsgType.TEXT:
                        await upstream_ws.send_str(msg.data)
                    elif msg.type == web.WSMsgType.BINARY:
                        await upstream_ws.send_bytes(msg.data)
                    elif msg.type == web.WSMsgType.PING:
                        await upstream_ws.ping(msg.data)
                    elif msg.type == web.WSMsgType.PONG:
                        await upstream_ws.pong(msg.data)
                    elif msg.type in (web.WSMsgType.CLOSE, web.WSMsgType.ERROR):
                        break
            except (aiohttp.ClientError, asyncio.CancelledError, ConnectionResetError):
                pass
            finally:
                if not upstream_ws.closed:
                    await upstream_ws.close()

        async def _n8n_to_browser() -> None:
            try:
                async for msg in upstream_ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        await ws.send_str(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        await ws.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.PING:
                        await ws.ping(msg.data)
                    elif msg.type == aiohttp.WSMsgType.PONG:
                        await ws.pong(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        break
            except (aiohttp.ClientError, asyncio.CancelledError, ConnectionResetError):
                pass
            finally:
                if not ws.closed:
                    await ws.close()

        await asyncio.gather(_browser_to_n8n(), _n8n_to_browser())
        await session.close()
        return ws

    # aiohttp routes each method to the matching handler; the proxy
    # accepts everything n8n's SPA uses. NOTE: no `options` handler —
    # Home Assistant's CORS setup adds its own preflight OPTIONS
    # handler to every route, and defining one here makes registration
    # fail with "already has OPTIONS handler". n8n's SPA talks to HA
    # same-origin, so it never sends a CORS preflight anyway.
    get = _handle
    post = _handle
    put = _handle
    patch = _handle
    delete = _handle
    head = _handle


class N8nProxyRootView(HomeAssistantView):
    """The proxy root (``/api/n8n_panel`` without a trailing slash)
    redirects to the trailing-slash form so relative URLs inside n8n
    resolve under the prefix. Same session-cookie gate as the proxy."""

    url = PROXY_PREFIX
    name = "n8n_panel:proxy_root"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.StreamResponse:
        if not _validate_session(self.hass, request):
            return web.Response(status=401, text="n8n Panel session required — reload the n8n panel.")
        raise web.HTTPFound(PROXY_PREFIX + "/")
