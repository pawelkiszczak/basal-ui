"""Anonymous, bounded public web documents for the Playground."""

import asyncio
import codecs
import http.client
import ipaddress
import json
import re
import socket
import ssl
import sys
from contextlib import suppress
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

MAX_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_REDIRECTS = 3
TIMEOUT_S = 10
REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(ValueError):
    def __init__(self, message, code="fetch_failed", status=502):
        super().__init__(message)
        self.code = code
        self.status = status


def _refuse(message):
    raise FetchError(message, "fetch_address_refused", 400)


def public_address(address):
    """Conservatively accept public unicast addresses, including on older Python versions."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 4:
        blocked = (
            "0.0.0.0/8",
            "10.0.0.0/8",
            "100.64.0.0/10",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "172.16.0.0/12",
            "192.0.0.0/24",
            "192.0.2.0/24",
            "192.88.99.0/24",
            "192.168.0.0/16",
            "198.18.0.0/15",
            "198.51.100.0/24",
            "203.0.113.0/24",
            "224.0.0.0/4",
            "240.0.0.0/4",
        )
        return ip.is_global and not any(ip in ipaddress.ip_network(net) for net in blocked)
    # Refuse translation, tunnelling and special-purpose ranges rather than trusting embedded IPv4.
    return (
        ip in ipaddress.ip_network("2000::/3")
        and ip not in ipaddress.ip_network("2001::/23")
        and ip not in ipaddress.ip_network("2001:db8::/32")
        and ip not in ipaddress.ip_network("2002::/16")
        and ip not in ipaddress.ip_network("3fff::/20")
        and ip.is_global
        and not (ip.is_multicast or ip.is_reserved or ip.is_private or ip.is_link_local)
    )


def checked_url(url):
    if not isinstance(url, str) or not url or re.search(r"[\x00-\x20\x7f]", url):
        _refuse("Enter an absolute HTTP or HTTPS URL without whitespace or control characters.")
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
            _refuse("Only absolute HTTP and HTTPS URLs are allowed.")
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            _refuse("URLs with credentials are not allowed.")
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if parsed.port == 0 or parsed.netloc.endswith(":") or "\\" in parsed.netloc or "%" in host:
            _refuse("The URL host or port is invalid.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            ascii_host = host.encode("idna").decode("ascii")
            labels = ascii_host.rstrip(".").split(".")
            if len(ascii_host) > 253 or any(
                not label or len(label) > 63 or not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?", label)
                for label in labels
            ):
                _refuse("The URL host is invalid.")
            host = ascii_host
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except FetchError:
        raise
    except (ValueError, UnicodeError, OSError) as exc:
        raise FetchError(f"Could not resolve URL: {exc}", "fetch_dns_error", 400) from exc
    addresses = [
        (family, sockaddr) for family, kind, _, _, sockaddr in answers if family in (socket.AF_INET, socket.AF_INET6)
    ]
    if not addresses or any(not public_address(sockaddr[0]) for _, sockaddr in addresses):
        _refuse("The URL resolves to a non-public address.")
    return parsed, host, port, addresses


class _Connection(http.client.HTTPConnection):
    def __init__(self, host, port, addresses, secure):
        super().__init__(host, port, timeout=TIMEOUT_S)
        self.addresses = addresses
        self.secure = secure
        if secure:
            self.default_port = 443

    def connect(self):
        error = None
        for family, sockaddr in self.addresses:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                if self.secure:
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)
                self.sock = sock
                return
            except OSError as exc:
                sock.close()
                error = exc
        raise error or OSError("No public address available")


class _ReadableHTML(HTMLParser):
    DROP = {"script", "style", "nav", "footer", "aside", "noscript", "svg"}
    BLOCK = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "div", "header", "section", "article", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title = [], []
        self.drop = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if self.drop:
            if tag in self.DROP:
                self.drop.append(tag)
        elif tag in self.DROP:
            self.drop.append(tag)
        elif tag == "title":
            self.in_title = True
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.drop:
            if tag == self.drop[-1]:
                self.drop.pop()
        elif tag == "title":
            self.in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.drop:
            (self.title if self.in_title else self.parts).append(data)


def _content(data, content_type):
    from email.message import Message

    message = Message()
    message["content-type"] = content_type
    kind = message.get_content_type().lower()
    try:
        encoding = codecs.lookup(message.get_content_charset() or "utf-8").name
    except LookupError:
        encoding = "utf-8"
    if kind == "application/json" or kind.endswith("+json"):

        def reject_constant(value):
            raise ValueError(f"Nonfinite JSON value: {value}")

        def safe_number(value):
            number = Decimal(value)
            converted = float(value)
            if (
                (number.is_zero() and number.is_signed())
                or (number == number.to_integral_value() and abs(number) > 9007199254740991)
                or Decimal(str(converted)) != number
            ):
                raise FetchError("JSON contains a number that cannot be preserved safely.", "fetch_json_precision", 422)
            return int(value) if "." not in value and "e" not in value.lower() else converted

        try:
            value = json.loads(
                data.decode(encoding), parse_int=safe_number, parse_float=safe_number, parse_constant=reject_constant
            )
            rendered = json.dumps(value, ensure_ascii=False, allow_nan=False)
            rendered.encode("utf-8")
        except FetchError:
            raise
        except (ValueError, UnicodeError, RecursionError, InvalidOperation) as exc:
            raise FetchError(f"Invalid JSON response: {exc}", "fetch_invalid_json", 502) from exc
        if len(rendered) > MAX_TEXT_CHARS:
            raise FetchError("JSON response exceeds the state limit.", "fetch_too_large", 413)
        return value, False
    if kind in {"text/html", "application/xhtml+xml"}:
        parser = _ReadableHTML()
        parser.feed(data.decode(encoding, errors="replace"))
        title = " ".join("".join(parser.title).split())
        lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip()]
        value = "\n".join(([f"Title: {title}"] if title else []) + lines)
    elif kind.startswith("text/"):
        value = data.decode(encoding, errors="replace")
    else:
        raise FetchError(f"Unsupported content type: {kind}", "fetch_unsupported_type", 415)
    return value[:MAX_TEXT_CHARS], len(value) > MAX_TEXT_CHARS


def fetch(url):
    """Run inside the owned child; the parent enforces its absolute deadline."""
    current = url
    for redirect in range(MAX_REDIRECTS + 1):
        parsed, host, port, addresses = checked_url(current)
        connection = _Connection(host, port, addresses, parsed.scheme.lower() == "https")
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            path = quote(path, safe="/%?=&;:+,@!$'()*-._~")
            connection.request(
                "GET", path, headers={"Accept": "text/html,application/json,text/*", "Accept-Encoding": "identity"}
            )
            response = connection.getresponse()
            if response.status in REDIRECTS:
                location = response.getheader("Location")
                if not location or re.search(r"[\x00-\x20\x7f]", location) or redirect == MAX_REDIRECTS:
                    raise FetchError("Invalid redirect or too many redirects.", "fetch_redirect_error")
                current = urljoin(current, location)
                continue
            if not 200 <= response.status < 300:
                raise FetchError(f"URL returned HTTP {response.status}.", "fetch_http_error")
            encoding = response.getheader("Content-Encoding", "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise FetchError(f"Unsupported content encoding: {encoding}", "fetch_unsupported_encoding", 415)
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise FetchError("Response exceeds the 4 MiB limit.", "fetch_too_large", 413)
            content_type = response.getheader("Content-Type", "application/octet-stream")
            state, truncated = _content(data, content_type)
            return {
                "url": url,
                "final_url": current,
                "content_type": content_type,
                "bytes": len(data),
                "truncated": truncated,
                "state": state,
            }
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            raise FetchError(f"URL request failed: {exc}", "fetch_failed") from exc
        finally:
            connection.close()
    raise FetchError("Too many redirects.", "fetch_redirect_error")


class FetchManager:
    """Own subprocesses until their request finishes, disconnects, times out, or the app stops."""

    def __init__(self):
        self.tasks = set()
        self.stopping = False

    async def run(self, url, request):
        if self.stopping:
            raise FetchError("The application is shutting down.", "fetch_cancelled", 503)
        task = asyncio.current_task()
        self.tasks.add(task)
        process = None
        spawn = None
        exchange = None
        deadline = asyncio.get_running_loop().time() + TIMEOUT_S
        try:
            try:
                spawn = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "basal_ui.fetch",
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                )
                while not spawn.done():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise FetchError("URL request timed out.", "fetch_timeout", 504)
                    if await request.is_disconnected():
                        raise FetchError("URL request cancelled.", "fetch_cancelled", 499)
                    await asyncio.wait({spawn}, timeout=min(0.1, remaining))
                process = await spawn
            except OSError as exc:
                raise FetchError("Could not start URL fetch worker.") from exc
            exchange = asyncio.create_task(process.communicate(json.dumps(url).encode()))
            while not exchange.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise FetchError("URL request timed out.", "fetch_timeout", 504)
                if await request.is_disconnected():
                    raise FetchError("URL request cancelled.", "fetch_cancelled", 499)
                await asyncio.wait({exchange}, timeout=min(0.1, remaining))
            stdout, _ = await exchange
            if asyncio.get_running_loop().time() > deadline:
                raise FetchError("URL request timed out.", "fetch_timeout", 504)
            if process.returncode:
                raise FetchError("URL fetch worker failed.")
            result = json.loads(stdout)
            if "error" in result:
                raise FetchError(result["error"], result["code"], result["status"])
            return result["result"]
        finally:
            if process is None and spawn is not None:
                spawn.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    process = await spawn
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                if exchange is not None:
                    await exchange
                else:
                    await process.communicate()
            self.tasks.discard(task)

    async def close(self):
        self.stopping = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    try:
        result = {"result": fetch(json.load(sys.stdin))}
    except FetchError as exc:
        result = {"error": str(exc), "code": exc.code, "status": exc.status}
    except Exception:
        result = {"error": "URL fetch worker failed.", "code": "fetch_failed", "status": 502}
    json.dump(result, sys.stdout, ensure_ascii=True, allow_nan=False)
