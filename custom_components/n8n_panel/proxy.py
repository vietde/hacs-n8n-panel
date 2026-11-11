"""Reverse proxy to n8n with correct subpath rewriting.

Home Assistant (server side) forwards the browser's requests to the
n8n instance on the LAN and rewrites n8n's responses so every URL runs
under the HA-side prefix (``/api/n8n_panel``). The browser only ever
talks to Home Assistant, so there is no mixed content and no CORS
problem — and because the rewriting is done here in code (not in
hass_ingress regex rules), the three failure modes that kept n8n from
loading through ingress do not apply:

1. ``window.BASE_PATH`` is set to the prefix WITH a trailing slash.
   n8n concatenates BASE_PATH + 'rest' with no separator, so without
   the slash the API goes to /api/n8n_panelrest/settings (404).
2. Vite lazy-chunk paths (``__vite__mapDeps`` entries like
   ``"assets/SigninView-*.js"``) are rewritten WITHOUT a leading
   slash. n8n's preload helper is ``$re = e => '/' + e`` — it prepends
   one slash itself. An absolute path would become protocol-relative
   ``//api/...`` and the browser would treat "api" as a host.
3. The session-cookie Path is scoped under the prefix (and never
   double-prefixed), so the browser sends it back on every request.
"""

from __future__ import annotations

import logging
import re

from .const import PROXY_PREFIX, PROXY_PREFIX_NOSLASH

_LOGGER = logging.getLogger(__name__)

# Hop-by-hop headers that must never be forwarded to the upstream or
# back to the browser.
_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

# Content types whose bodies are text and may carry URLs to rewrite.
_TEXT_TYPES = (
    "text/html",
    "application/javascript",
    "text/javascript",
    "text/css",
    "application/json",
)


def _is_text(content_type: str) -> bool:
    return any(t in content_type for t in _TEXT_TYPES)


def rewrite_body(body: bytes, content_type: str) -> bytes:
    """Rewrite n8n's response body so all its URLs run under the proxy
    prefix. Only text responses are touched; binary (fonts, images,
    wasm) passes through unchanged."""
    if not _is_text(content_type):
        return body
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body

    # 1. BASE_PATH, with the trailing slash n8n expects. The frontend
    # reads window.BASE_PATH and uses it for every API call (/rest/...),
    # the Vue Router base, and all URL construction.
    text = text.replace(
        "window.BASE_PATH = '/'", f"window.BASE_PATH = '{PROXY_PREFIX}/'"
    )

    # 2. Vite lazy-chunk paths in __vite__mapDeps. Quoted "assets/ (and
    # 'assets/) become api/n8n_panel/assets/ — WITHOUT a leading slash,
    # because $re prepends one. A quoted path cannot already be
    # prefixed (a prefixed one reads "/api/n8n_panel/assets/), so this
    # cannot double-prefix. All 420 quoted refs in n8n 2.42.7 live
    # inside the __vite__mapDeps array, so they all go through $re.
    text = re.sub(r'"assets/', f'"{PROXY_PREFIX_NOSLASH}/assets/', text)
    text = re.sub(r"'assets/", f"'{PROXY_PREFIX_NOSLASH}/assets/", text)

    # 3. Absolute asset paths in the HTML (/assets/..., /static/...,
    # /favicon.ico). The (?<!n8n_panel) guard skips URLs already
    # prefixed, so they are not prefixed a second time.
    text = re.sub(
        r"(?<!n8n_panel)/(assets|static)/", PROXY_PREFIX + r"/\1/", text
    )
    text = re.sub(
        r"(?<!n8n_panel)\\/(assets|static)\\/",
        PROXY_PREFIX + r"\/\1\/",
        text,
    )
    text = re.sub(
        r"(?<!n8n_panel)/favicon\.ico", PROXY_PREFIX + "/favicon.ico", text
    )
    text = re.sub(
        r"(?<!n8n_panel)\\/favicon\.ico",
        PROXY_PREFIX + r"\/favicon.ico",
        text,
    )

    # 4. CSS url() references to absolute asset paths.
    text = re.sub(
        r"url\(/(assets|static)/", f"url({PROXY_PREFIX}/\\1/", text
    )
    text = re.sub(
        r"url\(\\/(assets|static)\\/",
        PROXY_PREFIX + r"\/\1\/",
        text,
    )
    return text.encode("utf-8")


def rewrite_location(location: str) -> str:
    """Prefix an absolute-path redirect with the proxy prefix (skip it
    when it already carries the prefix)."""
    if location.startswith("/") and not location.startswith(PROXY_PREFIX):
        return PROXY_PREFIX + location
    return location


def rewrite_set_cookie(cookie: str) -> str:
    """Scope a Set-Cookie Path under the proxy prefix. A Path that
    already starts with the prefix is left alone (a second rewrite
    would store the cookie under a doubled path and the browser would
    never send it back)."""

    def _repl(match: re.Match[str]) -> str:
        path = match.group(1)
        if path.startswith(PROXY_PREFIX):
            return match.group(0)
        return f"Path={PROXY_PREFIX}{path}"

    return re.sub(r"Path=([^;,\s]*)", _repl, cookie)


def filter_request_headers(headers) -> dict[str, str]:
    """Headers to forward to n8n (hop-by-hop headers removed)."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _HOP_HEADERS
    }


def filter_response_headers(headers) -> dict[str, str]:
    """Headers to send back to the browser (hop-by-hop removed, and
    Content-Length dropped because the body may have been rewritten to
    a different length)."""
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in _HOP_HEADERS and name.lower() != "content-length"
    }
