"""Bounded, anonymous URL fetching for Playground state.

Every request (and every redirect) resolves its host, refuses any non-public address, and connects only to an
address that check accepted (_PinnedBackend), so a DNS answer that changes after the check (rebinding) cannot
redirect the socket to a private address. TLS still verifies the certificate against the URL's hostname.
"""

import codecs
import ipaddress
import json
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_REDIRECTS = 3
TIMEOUT_S = 10
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(ValueError):
    status_code = 502
    code = "fetch_failed"


class UnsafeAddress(FetchError):
    status_code = 400
    code = "fetch_address_refused"


class FetchTooLarge(FetchError):
    status_code = 413
    code = "fetch_too_large"


class UnsupportedContentType(FetchError):
    status_code = 415
    code = "fetch_unsupported_type"


class FetchHTTPError(FetchError):
    status_code = 502
    code = "fetch_http_error"


class _ReadableHTML(HTMLParser):
    _DROP = {"script", "style", "nav", "header", "footer", "aside", "noscript"}
    _BLOCK = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "div", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.title = []
        self.in_title = False
        self.drop = 0
        self.cells = []
        self.row = []

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self.drop += 1
        elif self.drop == 0:
            if tag == "title":
                self.in_title = True
            elif tag in {"td", "th"}:
                self.cells.append([])

    def handle_endtag(self, tag):
        if tag in self._DROP and self.drop:
            self.drop -= 1
        elif self.drop == 0:
            if tag == "title":
                self.in_title = False
            elif tag in {"td", "th"} and self.cells:
                cell = " ".join("".join(self.cells.pop()).split())
                self.row.append(cell)
            elif tag == "tr" and self.row:
                self.parts.append("\t".join(self.row))
                self.row = []
            elif tag in self._BLOCK:
                self.parts.append("\n")

    def handle_data(self, data):
        if self.drop:
            return
        if self.in_title:
            self.title.append(data)
        elif self.cells:
            self.cells[-1].append(data)
        else:
            self.parts.append(data)


def _public_address(address):
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    # is_global excludes private, loopback, link-local, CGNAT, multicast,
    # unspecified and reserved ranges; metadata IPs are explicitly rejected too.
    return ip.is_global and str(ip) not in {"169.254.169.254", "fd00:ec2::254"}


def _checked(url: str) -> tuple[str, list[str]]:
    """(hostname, its public addresses) of an HTTP(S) URL; UnsafeAddress when any address is not public."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise UnsafeAddress("only absolute http and https URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeAddress("URLs with embedded credentials are not allowed")
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        answers = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except UnsafeAddress:
        raise
    except (TypeError, ValueError, OSError) as exc:
        raise UnsafeAddress(f"could not resolve a safe address for URL: {exc}") from exc
    addresses = sorted({item[4][0] for item in answers})
    if not addresses or any(not _public_address(address) for address in addresses):
        raise UnsafeAddress(f"URL host resolves to a non-public address: {parsed.hostname}")
    return parsed.hostname, addresses


def guard(url: str) -> str:
    """Validate an HTTP(S) URL and resolve its host, rejecting any non-public address -> the URL's hostname."""
    return _checked(url)[0]


class _PinnedBackend(httpcore.SyncBackend):
    """httpcore's socket backend that connects `host` only to the addresses guard() accepted for it."""

    def __init__(self, host, addresses):
        self.host, self.addresses = host.lower(), addresses

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host.lower().strip("[]") != self.host.strip("[]"):
            raise httpcore.ConnectError(f"connection to unchecked host {host} refused")
        error = None
        for address in self.addresses:
            try:
                return super().connect_tcp(address, port, timeout, local_address, socket_options)
            except httpcore.ConnectError as exc:
                error = exc
        raise error


def _transport(host, addresses):
    transport = httpx.HTTPTransport(trust_env=False)
    transport._pool._network_backend = _PinnedBackend(host, addresses)  # httpcore.ConnectionPool's socket factory
    return transport


def _readable_html(data: bytes, encoding: str):
    parser = _ReadableHTML()
    parser.feed(data.decode(encoding, errors="replace"))
    title = " ".join("".join(parser.title).split())
    body = "".join(parser.parts)
    # Preserve block boundaries while removing excessive whitespace.
    body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    if parser.row:
        body += ("\n" if body else "") + "\t".join(parser.row)
    state = "\n".join(part for part in (f"Title: {title}" if title else "", body) if part)
    return state


def _content_state(response: httpx.Response, data: bytes):
    content_type = response.headers.get("content-type", "application/octet-stream").split(";", 1)[0].strip().lower()
    try:
        encoding = codecs.lookup(response.encoding or "utf-8").name
    except LookupError:
        encoding = "utf-8"
    if content_type in {"text/html", "application/xhtml+xml"}:
        return _readable_html(data, encoding)
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return json.loads(data.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise FetchHTTPError(f"invalid JSON response: {exc}") from exc
    if content_type.startswith("text/"):
        return data.decode(encoding, errors="replace")
    raise UnsupportedContentType(f"unsupported content type: {content_type}")


def fetch(url: str) -> dict:
    """Fetch one bounded anonymous document, manually validating each redirect target."""
    current = url
    deadline = time.monotonic() + TIMEOUT_S
    for redirect_count in range(MAX_REDIRECTS + 1):
        host, addresses = _checked(current)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchHTTPError("URL request timed out")
        try:
            client = httpx.Client(
                timeout=remaining, follow_redirects=False, trust_env=False, transport=_transport(host, addresses)
            )
            with client:  # noqa: SIM117 — the stream needs the open client
                with client.stream("GET", current, headers={"Accept": "text/html,application/json,text/*"}) as response:
                    if response.status_code in REDIRECTS:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchHTTPError("redirect response has no Location header")
                        if redirect_count >= MAX_REDIRECTS:
                            raise FetchHTTPError("too many redirects (maximum 3)")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise FetchHTTPError(f"URL returned HTTP {response.status_code}")
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        if time.monotonic() >= deadline:
                            raise FetchHTTPError("URL request timed out")
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise FetchTooLarge(f"response exceeds the {MAX_BYTES}-byte limit")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    state = _content_state(response, data)
                    truncated = False
                    if isinstance(state, str) and len(state) > MAX_TEXT_CHARS:
                        state = state[:MAX_TEXT_CHARS]
                        truncated = True
                    return {
                        "url": url,
                        "final_url": current,
                        "content_type": response.headers.get("content-type", "application/octet-stream"),
                        "bytes": size,
                        "truncated": truncated,
                        "state": state,
                    }
        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            raise FetchHTTPError("URL request timed out") from exc
        except httpx.HTTPError as exc:
            raise FetchHTTPError(f"URL request failed: {exc}") from exc
    raise FetchHTTPError("too many redirects (maximum 3)")
