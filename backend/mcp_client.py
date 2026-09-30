"""
Mermail MCP client — Streamable HTTP transport.

TRACK HARD GATE
---------------
This module is the project's required-technology integration for the Alexa+
track. Every inbox read in this repo goes through a *real* MCP session against
the hosted Mermail MCP server using the MCP Streamable HTTP transport:

    POST https://console.mermail.app/mcp?profile=agent-inbox
    JSON-RPC 2.0 messages over HTTP, SSE responses, Mcp-Session-Id affinity.

Nothing here is a README mention: importing this module and calling
``MermailMCPClient.connect()`` performs the MCP ``initialize`` handshake,
``MermailMCPClient.list_tools()`` performs MCP ``tools/list``, and
``MermailMCPClient.call_tool()`` performs MCP ``tools/call``. The protocol
shapes follow the MCP 2025-06-18 Streamable HTTP spec and Mermail's published
docs (https://docs.mermail.app/ai/skills).

Auth: ``MERMAIL_API_KEY`` environment variable, sent as the ``x-api-key``
header (API-key mode, per Mermail's mcp skill). Keys carry the ``sk-proj-``
prefix. Without a key the client raises :class:`MCPAuthError` — the app
refuses to fabricate inbox data.

Profile: ``?profile=agent-inbox`` selects Mermail's exact 12-tool
least-privilege profile (mailbox discovery, email search, email read). That
is the smallest allowlist the invoice-audit workflow needs; send, wallet,
and admin tools are absent by design, not by accident.
"""

from __future__ import annotations

import json
import os
import uuid

import httpx

MCP_BASE_URL = "https://console.mermail.app/mcp"
MCP_PROFILE = "agent-inbox"  # ?profile=agent-inbox -> exact 12-tool profile
MCP_PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "alexa-plus-invoice-radar"
CLIENT_VERSION = "0.1.0"


class MCPError(Exception):
    """Base class for MCP transport / protocol failures."""


class MCPAuthError(MCPError):
    """The server rejected our credentials (HTTP 401)."""


class MCPTransportError(MCPError):
    """HTTP-level failure talking to the MCP server."""


class MCPProtocolError(MCPError):
    """The server spoke, but not valid MCP (bad JSON-RPC / SSE)."""


def _proxy_url() -> str | None:
    """Proxy for the MCP connection, read explicitly from the environment.

    httpx's own env parsing crashes on some sandbox NO_PROXY values
    (bracketed IPv6 literals break its URLPattern parser), so the client
    reads the standard proxy variables itself and disables trust_env.
    Only the public Mermail endpoint is ever called, so no_proxy bypass
    lists are irrelevant here.
    """
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(var)
        if value:
            return value
    return None


def _parse_sse_events(text: str) -> list[dict]:
    """Parse a Server-Sent Events body into JSON-RPC message dicts.

    Handles multi-line ``data:`` payloads, ``:`` comment lines, and the
    ``[DONE]`` terminator. Raises MCPProtocolError on unparseable payloads.
    """
    events: list[dict] = []
    for chunk in text.split("\n\n"):
        data_lines = []
        for line in chunk.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            # event:/id:/retry: lines and ':' comments are intentionally ignored
        if not data_lines:
            continue
        payload = "\n".join(data_lines)
        if payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(f"unparseable SSE data payload: {payload[:120]!r}") from exc
    return events


def _unwrap_tool_result(result: dict):
    """Unwrap an MCP tools/call result into plain Python data.

    MCP returns ``{"content": [{"type": "text", "text": "..."}]}``; the text
    payload is usually JSON. Returns the parsed object, falling back to the
    raw content list when it is not JSON.
    """
    content = result.get("content", [])
    if len(content) == 1 and content[0].get("type") == "text":
        text = content[0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return content if content else result


class MermailMCPClient:
    """Stateful MCP session against the hosted Mermail MCP server."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = MCP_BASE_URL,
        profile: str = MCP_PROFILE,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("MERMAIL_API_KEY", "")
        self.base_url = f"{base_url}?profile={profile}" if profile else base_url
        self.profile = profile
        self._http = httpx.Client(timeout=timeout, trust_env=False, proxy=_proxy_url())
        self._session_id: str | None = None
        self._server_info: dict | None = None
        self._tools_cache: list[dict] | None = None

    # -- session lifecycle -------------------------------------------------

    def connect(self) -> dict:
        """Perform the MCP initialize handshake. Returns the server's result.

        Raises MCPAuthError when the API key is missing or rejected, so
        callers can never mistake "not connected" for "empty inbox".
        """
        if not self.api_key:
            raise MCPAuthError(
                "MERMAIL_API_KEY is not set. Create a project API key in the "
                "Mermail console (Settings -> API Keys) and export it; the "
                "client will not proceed without one."
            )
        if not self.api_key.startswith("sk-proj-"):
            raise MCPAuthError(
                "MERMAIL_API_KEY does not look like a Mermail project key "
                "(expected 'sk-proj-' prefix). Refusing to send a malformed key."
            )
        init_id = uuid.uuid4().hex
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": init_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
                },
            }
        )
        message = self._match_response(response, init_id)
        result = message.get("result", {})
        self._server_info = result.get("serverInfo", {})
        # MCP handshake completion: client MUST send notifications/initialized.
        self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        return result

    @property
    def server_info(self) -> dict | None:
        return self._server_info

    # -- MCP operations ----------------------------------------------------

    def list_tools(self, refresh: bool = False) -> list[dict]:
        """MCP tools/list. Returns the server's tool catalog (cached)."""
        if self._tools_cache is None or refresh:
            list_id = uuid.uuid4().hex
            message = self._match_response(
                self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": list_id,
                        "method": "tools/list",
                        "params": {},
                    }
                ),
                list_id,
            )
            self._tools_cache = message.get("result", {}).get("tools", [])
        return self._tools_cache

    def describe_tool(self, name: str) -> dict:
        """Return the live inputSchema for one tool (schema self-check)."""
        for tool in self.list_tools():
            if tool.get("name") == name:
                return tool.get("inputSchema", {})
        raise MCPError(f"tool {name!r} not in server catalog")

    def call_tool(self, name: str, arguments: dict):
        """MCP tools/call. Returns the unwrapped tool result."""
        call_id = uuid.uuid4().hex
        message = self._match_response(
            self._post(
                {
                    "jsonrpc": "2.0",
                    "id": call_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                }
            ),
            call_id,
        )
        if "error" in message:
            err = message["error"]
            raise MCPError(f"tools/call {name} failed: {err.get('message')} "
                           f"(code {err.get('code')})")
        return _unwrap_tool_result(message.get("result", {}))

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MermailMCPClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "x-api-key": self.api_key,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict) -> list[dict]:
        """POST one JSON-RPC message; return parsed response messages."""
        try:
            resp = self._http.post(self.base_url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise MCPTransportError(f"HTTP transport failed: {exc}") from exc

        if resp.status_code == 401:
            raise MCPAuthError(
                f"MCP server rejected the API key (HTTP 401): {resp.text[:200]}"
            )
        if resp.status_code == 400 and "invalid_mcp_tool_profile" in resp.text:
            raise MCPError(
                "server rejected the MCP tool profile selector "
                f"(profile={self.profile!r})"
            )
        if resp.status_code >= 400:
            raise MCPTransportError(
                f"MCP server HTTP {resp.status_code}: {resp.text[:200]}"
            )

        session_id = resp.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            return _parse_sse_events(resp.text)
        # Some servers answer plain JSON (e.g. error bodies, single responses).
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"non-SSE, non-JSON response: {resp.text[:200]!r}"
            ) from exc
        return [body] if isinstance(body, dict) else body

    @staticmethod
    def _match_response(messages: list[dict], call_id: str | None) -> dict:
        """Find the JSON-RPC response matching our request id.

        Notifications (no id) are skipped. A JSON-RPC error object is returned
        as-is so the caller can raise with the server's message.
        """
        for message in messages:
            if not isinstance(message, dict):
                continue
            if "error" in message and (call_id is None or message.get("id") == call_id):
                return message
            if call_id is None:
                if "result" in message:
                    return message
            elif message.get("id") == call_id:
                return message
        raise MCPProtocolError(
            f"no JSON-RPC response matching id {call_id!r} in {len(messages)} message(s)"
        )
