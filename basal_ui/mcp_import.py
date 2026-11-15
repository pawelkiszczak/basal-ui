"""Bounded streamable HTTP MCP catalogue import and local BM25 ranking."""

import http.client
import json
import math
import re
import sys
from collections import Counter
from decimal import Decimal
from urllib.parse import quote

from .fetch import MAX_BYTES, FetchError, _Connection, _explicit_loopback, checked_url

MAX_PAGES = 20
VERSIONS = {"2025-11-25", "2025-06-18", "2025-03-26"}
TOKEN = re.compile(r"\w+")


def fail(message, code="mcp_protocol_error", status=502):
    raise FetchError(message, code, status)


def decode(data):
    def number(value):
        n = Decimal(value)
        f = float(value)
        if (
            (n.is_zero() and n.is_signed())
            or (n == n.to_integral_value() and abs(n) > 9007199254740991)
            or Decimal(str(f)) != n
        ):
            fail("MCP response contains a number that cannot be preserved safely.", "mcp_json_precision", 422)
        return int(value) if "." not in value and "e" not in value.lower() else f

    try:
        return json.loads(
            data, parse_int=number, parse_float=number, parse_constant=lambda _: fail("Invalid JSON number.")
        )
    except FetchError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        fail("MCP server returned invalid JSON.")


def list_tools(body):
    if not isinstance(body, dict):
        fail("Request must be an object.", "mcp_invalid_request", 400)
    token = body.get("token")
    if token is not None and (not isinstance(token, str) or not token or not re.fullmatch(r"[\x21-\x7e]+", token)):
        fail("Bearer token must contain only visible ASCII characters.", "mcp_invalid_token", 400)
    cursor = body.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        fail("Cursor must be a nonempty string.", "mcp_invalid_request", 400)
    parsed, host, port, addresses = checked_url(body.get("url"), allow_loopback=True)
    local = host.lower() == "localhost" or _explicit_loopback(host)
    if token and not local and parsed.scheme.lower() != "https":
        fail("Use HTTPS for a remote bearer token.", "mcp_insecure_auth", 400)
    path = quote((parsed.path or "/") + ("?" + parsed.query if parsed.query else ""), safe="/%?=&;:+,@!$'()*-._~")
    headers = {
        "Accept": "application/json, text/event-stream",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    budget = MAX_BYTES
    session = None

    def request(method, params=None, ident=None):
        nonlocal budget, session
        conn = _Connection(host, port, addresses, parsed.scheme.lower() == "https")
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if ident is not None:
            payload["id"] = ident
        try:
            conn.request("POST", path, json.dumps(payload).encode(), headers)
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                fail(
                    f"MCP endpoint returned HTTP {response.status}.",
                    "mcp_auth_error" if response.status in (401, 403) else "mcp_http_error",
                )
            if response.getheader("Content-Encoding", "identity").strip().lower() not in ("", "identity"):
                fail("MCP endpoint returned unsupported content encoding.", "mcp_encoding_error")
            new_session = response.getheader("Mcp-Session-Id")
            if new_session:
                if not re.fullmatch(r"[\x21-\x7e]+", new_session) or (session and session != new_session):
                    fail("MCP server returned an invalid session ID.")
                session = new_session
                headers["Mcp-Session-Id"] = session
            if ident is None:
                return None
            kind = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if kind == "text/event-stream":
                lines = []
                while True:
                    line = response.readline(budget + 1)
                    budget -= len(line)
                    if budget < 0:
                        fail("MCP responses exceed 4 MiB.", "mcp_too_large", 413)
                    if not line:
                        fail("MCP stream ended before its response.")
                    if line in (b"\n", b"\r\n"):
                        if lines:
                            message = decode(b"\n".join(lines))
                            lines = []
                            if isinstance(message, dict) and message.get("id") == ident:
                                break
                    elif line.startswith(b"data:"):
                        value = line[5:].rstrip(b"\r\n")
                        lines.append(value[1:] if value.startswith(b" ") else value)
            elif kind == "application/json" or kind.endswith("+json"):
                data = response.read(budget + 1)
                budget -= len(data)
                if budget < 0:
                    fail("MCP responses exceed 4 MiB.", "mcp_too_large", 413)
                message = decode(data)
            else:
                fail("MCP endpoint returned an unsupported content type.")
            if (
                not isinstance(message, dict)
                or message.get("jsonrpc") != "2.0"
                or type(message.get("id")) is not int
                or message["id"] != ident
            ):
                fail("MCP server returned a mismatched JSON-RPC response.")
            if "error" in message:
                fail("MCP server returned a JSON-RPC error.")
            if not isinstance(message.get("result"), dict):
                fail("MCP server returned an invalid JSON-RPC result.")
            return message["result"]
        except FetchError:
            raise
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            fail("Could not contact the MCP endpoint.", "mcp_connection_error")
        finally:
            conn.close()

    try:
        init = request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "basal-ui", "version": "0"}},
            1,
        )
        version = init.get("protocolVersion")
        if version not in VERSIONS:
            fail("MCP server negotiated an unsupported protocol version.")
        headers["MCP-Protocol-Version"] = version
        request("notifications/initialized")
        tools, names, cursors = [], set(), set()
        if cursor is not None:
            cursors.add(cursor)
        for page in range(MAX_PAGES):
            result = request("tools/list", {"cursor": cursor} if cursor is not None else {}, page + 2)
            items = result.get("tools")
            if not isinstance(items, list):
                fail("MCP tools/list did not return a tools array.")
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not item["name"]
                    or any(c.isspace() for c in item["name"])
                    or item["name"] in names
                    or not isinstance(item.get("inputSchema"), dict)
                    or ("description" in item and not isinstance(item["description"], str))
                ):
                    fail("MCP server returned an invalid or duplicate tool definition.")
                schema = item["inputSchema"]
                props = schema.get("properties", {})
                required = schema.get("required", [])
                if (
                    schema.get("type", "object") != "object"
                    or not isinstance(props, dict)
                    or any(not isinstance(value, (dict, bool)) for value in props.values())
                    or not isinstance(required, list)
                    or not all(isinstance(key, str) for key in required)
                    or len(set(required)) != len(required)
                    or any(key not in props for key in required)
                ):
                    fail("MCP server returned an invalid tool schema.")
                names.add(item["name"])
                tools.append({key: item[key] for key in ("name", "description", "inputSchema") if key in item})
            cursor = result.get("nextCursor")
            if cursor is None:
                return {"tools": tools}
            if not isinstance(cursor, str) or not cursor or cursor in cursors:
                fail("MCP server returned an invalid or repeated cursor.")
            cursors.add(cursor)
        fail("MCP catalogue exceeds 20 pages.", "mcp_page_limit", 413)
    finally:
        if session:
            conn = _Connection(host, port, addresses, parsed.scheme.lower() == "https")
            try:
                conn.request("DELETE", path, headers=headers)
                conn.getresponse().close()
            except (OSError, http.client.HTTPException):
                pass
            finally:
                conn.close()


def shortlist(tools, query, k=40):
    if not isinstance(tools, list) or not isinstance(query, str) or type(k) is not int or not 1 <= k <= 255:
        raise ValueError("Provide tools, a query string and an integer k between 1 and 255.")
    documents = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("Each tool must be an object.")
        definition = tool.get("function", tool)
        if (
            not isinstance(definition, dict)
            or not isinstance(definition.get("name"), str)
            or definition.get("description") is not None
            and not isinstance(definition.get("description"), str)
        ):
            raise ValueError("Each tool must have a name and optional description.")
        words = TOKEN.findall((definition["name"] + " " + (definition.get("description") or "")).lower())
        documents.append((Counter(words), len(words)))
    terms = set(TOKEN.findall(query.lower()))
    df = Counter(term for counts, _ in documents for term in counts if term in terms)
    average = sum(length for _, length in documents) / len(documents) if documents else 0

    def score(doc):
        counts, length = doc
        if not average:
            return 0
        norm = 1.2 * (0.25 + 0.75 * length / average)
        return sum(
            math.log(1 + (len(documents) - df[t] + 0.5) / (df[t] + 0.5)) * counts[t] * 2.2 / (counts[t] + norm)
            for t in terms
            if counts[t]
        )

    return [tools[i] for i in sorted(range(len(tools)), key=lambda i: -score(documents[i]))[:k]]


if __name__ == "__main__":
    try:
        result = {"result": list_tools(json.load(sys.stdin))}
    except FetchError as exc:
        result = {"error": str(exc), "code": exc.code, "status": exc.status}
    except Exception:
        result = {"error": "MCP import worker failed.", "code": "mcp_failed", "status": 502}
    json.dump(result, sys.stdout, ensure_ascii=True, allow_nan=False)
