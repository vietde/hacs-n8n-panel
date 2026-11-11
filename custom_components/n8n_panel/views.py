"""HTTP views: the n8n panel asset and the reverse proxy to n8n.

The sidebar panel is a custom element (panel.js) that opens a full-size
iframe to the proxy root (``/api/n8n_panel/``). The iframe loads n8n's
own SPA through the proxy, so the user gets the real n8n UI — sign-in,
workflows, executions — inside Home Assistant, and logs in with their
n8n credentials there.

The proxy forwards every request to the n8n instance on the LAN and
rewrites the responses (see proxy.py) so all of n8n's URLs run under
the HA-side prefix. WebSocket upgrades (n8n's live-push channel at
``/rest/push``) are relayed frame-by-frame.

All proxy routes require a logged-in HA user (``requires_auth`` is the
default); the browser sends its HA session cookie automatically, and
n8n's own session cookie is scoped under the proxy prefix by the
Set-Cookie rewrite, so the browser sends it back on every proxied
request.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_PORT, HA_ASSET_PREFIX, PROXY_PREFIX
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


class N8nProxyView(HomeAssistantView):
    """Reverse proxy to the n8n instance on the LAN.

    Catches every path under the proxy prefix and forwards it to n8n,
    rewriting the response so n8n's URLs run under the prefix. Also
    relays WebSocket upgrades (n8n's /rest/push live channel).
    """

    url = PROXY_PREFIX + "/{path:.*}"
    name = "n8n_panel:proxy"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def _handle(self, request: web.Request) -> web.StreamResponse:
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
                    out_body = rewrite_body(raw, content_type)
                    response = web.Response(
                        status=resp.status,
                        body=out_body,
                        headers=out_headers,
                        content_type=content_type.split(";")[0] or None,
                    )
                    for cookie in rewritten:
                        response.headers.add("Set-Cookie", cookie)
                    return response
                out_body = rewrite_body(raw, content_type)
                return web.Response(
                    status=resp.status,
                    body=out_body,
                    headers=out_headers,
                    content_type=content_type.split(";")[0] or None,
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
    # accepts everything n8n's SPA uses.
    get = _handle
    post = _handle
    put = _handle
    patch = _handle
    delete = _handle
    head = _handle
    options = _handle


class N8nProxyRootView(HomeAssistantView):
    """The proxy root (``/api/n8n_panel`` without a trailing slash)
    redirects to the trailing-slash form so relative URLs inside n8n
    resolve under the prefix."""

    url = PROXY_PREFIX
    name = "n8n_panel:proxy_root"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        raise web.HTTPFound(PROXY_PREFIX + "/")
