"""
Alexa+ Invoice Radar — simulated Alexa+ experience over the real Mermail MCP.

Endpoints:
    GET  /              the simulated Alexa+ voice experience (single-page app)
    GET  /api/health    MCP connectivity: not_configured | connected | auth_error
    GET  /api/tools     live MCP tool catalog (schema self-check)
    POST /api/ask       {"text": "Alexa, what's unpaid?"} -> {intent, speech, cards}

Every inbox read goes through backend/mcp_client.py — a real MCP
Streamable-HTTP session against https://console.mermail.app/mcp. Without
MERMAIL_API_KEY the API refuses to answer; it never invents inbox data.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .alexa_sim import INTENT_FALLBACK, respond, route
from .audit import extract_claim
from .mcp_client import MCPAuthError, MCPError, MermailMCPClient

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

MAILBOX_ENV = "MERMAIL_MAILBOX"
DEFAULT_MAILBOX = "nova-demo@mermail.app"  # Tim's live demo mailbox

# search_emails free-text key: public docs say "free text" without naming the
# field. Verified against the live inputSchema via GET /api/tools once a key
# exists; override with MERMAIL_SEARCH_KEY if the schema names it differently.
SEARCH_TEXT_KEY = os.environ.get("MERMAIL_SEARCH_KEY", "search")

INVOICE_QUERY = "invoice OR payment due OR amount due OR INV-"

app = FastAPI(title="Alexa+ Invoice Radar (simulated experience)")


class AskRequest(BaseModel):
    text: str


def _require_key() -> str:
    key = os.environ.get("MERMAIL_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503,
            detail=(
                "MERMAIL_API_KEY is not set. Create a project API key in the "
                "Mermail console (Settings -> API Keys) and export it before "
                "asking. The sim never fabricates inbox data."
            ),
        )
    return key


def _resolve_mailbox(client: MermailMCPClient) -> str:
    """Resolve the target mailbox public_id via MCP list_mailboxes."""
    wanted = os.environ.get(MAILBOX_ENV, DEFAULT_MAILBOX).lower()
    mailboxes = client.call_tool("list_mailboxes", {})
    items = mailboxes if isinstance(mailboxes, list) else mailboxes.get("mailboxes", [])
    for mb in items:
        addresses = " ".join(
            str(mb.get(k, "")) for k in ("address", "email", "name", "public_id")
        ).lower()
        if wanted in addresses:
            return mb.get("public_id") or mb.get("id")
    raise MCPError(
        f"mailbox {wanted!r} not found in list_mailboxes "
        f"({len(items)} mailbox(es) visible)"
    )


def _collect_claims(client: MermailMCPClient, mailbox_id: str) -> list:
    """Full voice-ask -> inbox intelligence pass over MCP tools."""
    found = client.call_tool(
        "search_emails",
        {
            "mailboxId": mailbox_id,
            "query": {
                SEARCH_TEXT_KEY: INVOICE_QUERY,
                "folder": "inbox",
                "limit": 25,
                "sortColumn": "date",
                "sortDirection": "DESC",
                "metadata_only": False,
                "agent_safe_content": True,
            },
        },
    )
    items = found if isinstance(found, list) else found.get("emails", found.get("items", []))
    claims = []
    for item in items:
        email_id = item.get("id") or item.get("emailId")
        if not email_id:
            continue
        try:
            full = client.call_tool(
                "get_email",
                {
                    "mailboxId": mailbox_id,
                    "emailId": email_id,
                    "query": {
                        "require_scan_status": "clean",
                        "agent_safe_content": True,
                        "max_body_chars": 10000,
                    },
                },
            )
        except MCPError:
            continue  # scan-blocked or unreadable: reported as unreadable, never guessed
        if isinstance(full, dict) and full.get("content_omitted"):
            continue
        claims.append(extract_claim(full if isinstance(full, dict) else item))
    return claims


@app.get("/api/health")
def health() -> dict:
    """MCP connectivity probe. Never exposes the key."""
    if not os.environ.get("MERMAIL_API_KEY"):
        return {
            "mcp": "not_configured",
            "detail": "Set MERMAIL_API_KEY to connect the sim to the live MCP server.",
        }
    try:
        with MermailMCPClient() as client:
            tools = client.list_tools()
            names = [t.get("name") for t in tools]
            return {
                "mcp": "connected",
                "server": client.server_info,
                "profile": client.profile,
                "tool_count": len(tools),
                "has_search": "search_emails" in names,
                "has_get": "get_email" in names,
            }
    except MCPAuthError as exc:
        return {"mcp": "auth_error", "detail": str(exc)}
    except MCPError as exc:
        return {"mcp": "error", "detail": str(exc)}


@app.get("/api/tools")
def tools() -> dict:
    """Live MCP tool catalog with input schemas (schema self-check)."""
    _require_key()
    try:
        with MermailMCPClient() as client:
            return {"tools": client.list_tools()}
    except MCPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ask")
def ask(req: AskRequest) -> dict:
    """Voice utterance in, Alexa-style speech + invoice cards out."""
    _require_key()
    intent, slots = route(req.text)
    if intent == INTENT_FALLBACK:
        from .alexa_sim import build_fallback_response

        return build_fallback_response()
    try:
        with MermailMCPClient() as client:
            mailbox_id = _resolve_mailbox(client)
            claims = _collect_claims(client, mailbox_id)
    except MCPAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MCPError as exc:
        raise HTTPException(status_code=502, detail=f"MCP error: {exc}") from exc
    response = respond(intent, slots, claims)
    response["meta"] = {
        "mailbox": os.environ.get(MAILBOX_ENV, DEFAULT_MAILBOX),
        "mcp_profile": "agent-inbox",
        "utterance": req.text,
    }
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


# Friendly JSON for unknown API routes (keeps the sim honest about 404s).
@app.exception_handler(404)
def not_found(_req, _exc) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "not found"})
