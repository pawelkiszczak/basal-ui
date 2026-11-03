"""Import MCP streamable-HTTP tool definitions and rank them by lexical relevance."""

import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

import httpx

from . import fetch

MAX_BYTES = 4 * 1024 * 1024
MAX_PAGES = 20
TIMEOUT_S = 10
PROTOCOL_VERSION = "2025-06-18"
_TOKEN = re.compile(r"\w+", re.UNICODE)


class MCPImportError(ValueError):
    status_code = 502


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _decode_response(response: httpx.Response, expected_id):
    body = response.content
    if len(body) > MAX_BYTES:
        raise MCPImportError("MCP response exceeds 4 MiB")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    try:
        if content_type == "text/event-stream":
            payloads = [
                line[5:].strip()
                for line in body.decode("utf-8").splitlines()
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")
            ]
            messages = [json.loads(payload) for payload in payloads]
        elif content_type == "application/json" or content_type.endswith("+json"):
            messages = [json.loads(body)]
        else:
            raise MCPImportError(f"MCP server returned unsupported content type: {content_type or 'missing'}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPImportError("MCP server returned invalid JSON-RPC data") from exc
    for message in messages:
        if isinstance(message, dict) and message.get("id") == expected_id:
            if "error" in message:
                detail = message["error"]
                text = detail.get("message", "JSON-RPC error") if isinstance(detail, dict) else str(detail)
                raise MCPImportError(str(text))
            result = message.get("result")
            if isinstance(result, dict):
                return result
            raise MCPImportError("MCP server returned an invalid JSON-RPC result")
    if response.status_code < 200 or response.status_code >= 300:
        raise MCPImportError(f"MCP server returned HTTP {response.status_code}")
    raise MCPImportError("MCP server response did not contain the matching JSON-RPC id")


def _post(client, url, method, params, request_id, headers):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        payload["id"] = request_id
    try:
        with client.stream("POST", url, json=payload, headers=headers) as response:
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise MCPImportError("MCP response exceeds 4 MiB")
                chunks.append(chunk)
            response._content = b"".join(chunks)
            if request_id is None:
                if response.status_code < 200 or response.status_code >= 300:
                    raise MCPImportError(f"MCP server returned HTTP {response.status_code}")
                return None, response.headers.get("Mcp-Session-Id")
            return _decode_response(response, request_id), response.headers.get("Mcp-Session-Id")
    except httpx.HTTPError as exc:
        raise MCPImportError(f"could not contact MCP server: {exc}") from exc


def list_tools(url: str, token: str | None = None, *, client_factory=httpx.Client) -> list[dict]:
    """Initialize a streamable-HTTP MCP session and return its paginated tools/list catalogue."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError as exc:
        raise MCPImportError(f"invalid MCP URL: {exc}") from exc
    if parsed.scheme.lower() not in ("http", "https") or not host:
        raise MCPImportError("MCP URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise MCPImportError("MCP URLs with embedded credentials are not allowed")
    if _loopback(host):
        transport = httpx.HTTPTransport(trust_env=False)
    else:
        try:
            checked_host, addresses = fetch._checked(url)
        except fetch.FetchError:
            raise
        except (OSError, ValueError) as exc:
            raise fetch.UnsafeAddress(str(exc)) from exc
        transport = fetch._transport(checked_host, addresses)
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = httpx.Timeout(TIMEOUT_S)
    client = client_factory(timeout=timeout, follow_redirects=False, trust_env=False, transport=transport)
    with client:
        init, session_id = _post(
            client,
            url,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "basal-ui", "version": "0"},
            },
            1,
            headers,
        )
        negotiated = init.get("protocolVersion")
        if negotiated != PROTOCOL_VERSION:
            raise MCPImportError(f"MCP server negotiated unsupported protocol version: {negotiated}")
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        _, updated_session = _post(client, url, "notifications/initialized", {}, None, headers)
        if updated_session:
            headers["Mcp-Session-Id"] = updated_session
        tools = []
        cursor = None
        request_id = 2
        for page in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor is not None else {}
            result, updated_session = _post(client, url, "tools/list", params, request_id, headers)
            if updated_session:
                headers["Mcp-Session-Id"] = updated_session
            page_tools = result.get("tools")
            if not isinstance(page_tools, list):
                raise MCPImportError("MCP server tools/list result has no tools array")
            for tool in page_tools:
                if (
                    not isinstance(tool, dict)
                    or not isinstance(tool.get("name"), str)
                    or not tool["name"]
                    or (tool.get("description") is not None and not isinstance(tool["description"], str))
                    or not isinstance(tool.get("inputSchema"), dict)
                ):
                    raise MCPImportError("MCP server returned an invalid tool definition")
                tools.append(
                    {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "inputSchema": tool["inputSchema"],
                    }
                )
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if page == MAX_PAGES - 1:
                raise MCPImportError(f"MCP tools/list exceeds {MAX_PAGES} pages")
            request_id += 1
    return tools


def _tokens(tool):
    name = tool.get("name", "")
    description = tool.get("description", "")
    return _TOKEN.findall(f"{name} {description}".lower())


def shortlist(tools: list[dict], query: str, k: int = 40) -> list[dict]:
    """Return the top-k tools by BM25, retaining input order when scores tie."""
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if not isinstance(tools, list):
        raise ValueError("tools must be an array")
    tokenized = [_tokens(tool) for tool in tools]
    query_terms = set(_TOKEN.findall(query.lower()))
    if not query_terms:
        return tools[:k]
    average_length = sum(map(len, tokenized)) / len(tokenized) if tokenized else 0
    if not average_length:
        return tools[:k]
    document_frequency = {term: sum(term in tokens for tokens in tokenized) for term in query_terms}
    scored = []
    for index, tokens in enumerate(tokenized):
        frequencies = {term: tokens.count(term) for term in query_terms}
        length_factor = 1.2 * (1 - 0.75 + 0.75 * len(tokens) / average_length)
        score = sum(
            math.log(1 + (len(tokenized) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            * (freq * 2.2 / (freq + length_factor))
            for term, freq in frequencies.items()
            if freq
        )
        scored.append((-score, index, tools[index]))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [tool for _, _, tool in scored[:k]]
