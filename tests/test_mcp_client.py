"""Tests for backend/mcp_client.py — the MCP Streamable-HTTP client.

Unit tests cover SSE parsing, response matching, and tool-result unwrapping.
Live tests hit the REAL https://console.mermail.app/mcp endpoint:

- test_live_unauthenticated_is_rejected: no key -> the real server must
  answer 401, and the client must surface MCPAuthError. This proves the
  client talks to the genuine endpoint and enforces the auth boundary.
- test_live_full_session: requires MERMAIL_API_KEY; skipped otherwise.
  Performs initialize -> tools/list and asserts the agent-inbox profile
  exposes the tools the audit workflow needs.
"""

import os

import pytest

from backend.mcp_client import (
    MCPAuthError,
    MCPProtocolError,
    MermailMCPClient,
    _parse_sse_events,
    _unwrap_tool_result,
)

_match_response = MermailMCPClient._match_response

LIVE = "https://console.mermail.app/mcp"


# -- SSE parsing -----------------------------------------------------------


def test_sse_single_json_rpc_message():
    body = 'event: message\ndata: {"jsonrpc":"2.0","id":"1","result":{"ok":true}}\n\n'
    events = _parse_sse_events(body)
    assert events == [{"jsonrpc": "2.0", "id": "1", "result": {"ok": True}}]


def test_sse_multiple_events_and_comments():
    body = (
        ": ping\n\n"
        'data: {"jsonrpc":"2.0","id":"a","result":{}}\n\n'
        'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
        "data: [DONE]\n\n"
    )
    events = _parse_sse_events(body)
    assert len(events) == 2
    assert events[0]["id"] == "a"
    assert events[1]["method"] == "notifications/progress"


def test_sse_multiline_data_joins():
    body = 'data: {"jsonrpc":"2.0",\ndata: "id":"9","result":{}}\n\n'
    events = _parse_sse_events(body)
    assert events[0]["id"] == "9"


def test_sse_empty_body_yields_no_events():
    assert _parse_sse_events("") == []


def test_sse_invalid_json_raises_protocol_error():
    with pytest.raises(MCPProtocolError):
        _parse_sse_events("data: not-json\n\n")


# -- response matching -----------------------------------------------------


def test_match_response_by_id_skips_notifications():
    messages = [
        {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}},
        {"jsonrpc": "2.0", "id": "x1", "result": {"serverInfo": {"name": "m"}}},
    ]
    assert _match_response(messages, "x1")["result"]["serverInfo"]["name"] == "m"


def test_match_response_returns_error_object():
    messages = [{"jsonrpc": "2.0", "id": "e1", "error": {"code": -32602, "message": "bad"}}]
    assert _match_response(messages, "e1")["error"]["code"] == -32602


def test_match_response_missing_id_raises():
    with pytest.raises(MCPProtocolError):
        _match_response([{"jsonrpc": "2.0", "id": "zzz", "result": {}}], "nope")


# -- tool result unwrapping ------------------------------------------------


def test_unwrap_json_text_content():
    result = {"content": [{"type": "text", "text": '{"emails": [1, 2]}'}], "isError": False}
    assert _unwrap_tool_result(result) == {"emails": [1, 2]}


def test_unwrap_plain_text_content_passes_through():
    result = {"content": [{"type": "text", "text": "hello"}]}
    assert _unwrap_tool_result(result) == "hello"


def test_unwrap_multi_block_returns_content():
    result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert _unwrap_tool_result(result) == result["content"]


# -- client construction ---------------------------------------------------


def test_client_requires_or_reads_env_key(monkeypatch):
    monkeypatch.delenv("MERMAIL_API_KEY", raising=False)
    with pytest.raises(MCPAuthError, match="MERMAIL_API_KEY is not set"):
        MermailMCPClient(api_key="").connect()
    with pytest.raises(MCPAuthError, match="sk-proj-"):
        MermailMCPClient(api_key="garbage").connect()


def test_client_url_selects_agent_inbox_profile():
    c = MermailMCPClient(api_key="sk-proj-test")
    assert c.base_url == LIVE + "?profile=agent-inbox"
    c.close()


# -- live endpoint ---------------------------------------------------------


def test_live_unauthenticated_is_rejected():
    """The real server must reject a keyless initialize with 401.

    Verified 2026-09-29: endpoint returns HTTP 401 {"error":"Unauthorized"}.
    This test pins that contract — transport live, auth enforced.
    """
    client = MermailMCPClient(api_key="sk-proj-deliberately-invalid-key")
    try:
        with pytest.raises(MCPAuthError):
            client.connect()
    finally:
        client.close()


@pytest.mark.skipif(
    not os.environ.get("MERMAIL_API_KEY"),
    reason="needs MERMAIL_API_KEY for a live authenticated session",
)
def test_live_full_session():
    """initialize -> tools/list against the real MCP server."""
    with MermailMCPClient() as client:
        tools = client.list_tools()
        names = {t["name"] for t in tools}
        assert "search_emails" in names
        assert "get_email" in names
        schema = client.describe_tool("search_emails")
        assert schema.get("type") == "object"
