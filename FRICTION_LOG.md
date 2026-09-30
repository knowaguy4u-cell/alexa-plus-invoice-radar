# FRICTION LOG — Alexa+ Invoice Radar

What broke, surprised, or slowed us down while building this entry — recorded
honestly, because the track awards up to +10% for a friction log and because
every entry here is a product suggestion for the platform owner.

Format per entry: date · what we tried · what happened (expected vs actual) ·
severity · workaround · actionable suggestion.

---

## 1. MCP endpoint answers, but only up to the auth boundary — 2026-09-29

- **Tried:** unauthenticated `POST https://console.mermail.app/mcp` with a
  well-formed MCP `initialize` payload, to confirm the transport before writing
  the client.
- **Expected:** either a protocol error (wrong transport) or a clear auth
  rejection.
- **Actual:** first attempt got HTTP 000 (connection failed — transient,
  retry succeeded), then HTTP 401 `{"error":"Unauthorized"}` in 0.9s.
- **Severity:** low — the 401 is the *correct* answer; it confirmed the
  endpoint is live and speaking HTTP at the right path.
- **Workaround:** none needed. The client (`backend/mcp_client.py`) treats
  401 as `MCPAuthError` and refuses to proceed — the app never invents inbox
  data.
- **Suggestion:** a `WWW-Authenticate`-style hint in the 401 body naming the
  expected header (`x-api-key`) would save every first-time integrator one
  docs round-trip.

## 2. `pip install mcp` blocked by PEP 668 — 2026-09-29

- **Tried:** installing the official `mcp` Python SDK to build the client.
- **Expected:** `pip install mcp` just works.
- **Actual:** refused — "externally managed environment" (PEP 668); the
  sandbox Python is not pip-writable and creating throwaway venvs for a
  judges-will-clone-this repo adds setup friction.
- **Severity:** low — became a design win.
- **Workaround:** hand-rolled the MCP Streamable-HTTP client
  (`backend/mcp_client.py`) on `httpx`, which was already installed. The
  protocol surface we need (initialize → notifications/initialized →
  tools/list → tools/call, SSE parsing, `Mcp-Session-Id`) fits in one file
  with zero new dependencies, and `requirements.txt` stays judges-friendly.
- **Suggestion:** none — the spec is simple enough that a minimal client is
  the right call for a demo-sized integration.

## 3. `search_emails` free-text query key is not pinned in public docs — 2026-09-29

- **Tried:** reading the mermail-manage-inbox skill's tool reference to learn
  the exact `search_emails` argument shape.
- **Expected:** a named field for the free-text query (e.g. `search`, `q`).
- **Actual:** the docs say `query` accepts "free text" without naming the
  field, and the example uses `query: { folder: "inbox", page: 1 }` with no
  text search at all.
- **Severity:** medium — a wrong key name fails silently (returns unfiltered
  results) at demo time, which is the worst kind of bug.
- **Workaround:** `MERMAIL_SEARCH_KEY` env override (default `"search"`) plus
  a schema self-check: `GET /api/tools` dumps the live `inputSchema` from
  `tools/list`, and the server log prints it at startup, so the real field
  name is one authenticated call away from confirmation.
- **Suggestion:** Mermail docs should name the free-text field (or accept
  several aliases server-side) — this is exactly the "integrator hit a wall
  in the first 20 minutes" detail a friction log exists to report.

## 4. Sandbox NO_PROXY entries crash httpx's env parsing — 2026-09-29

- **Tried:** constructing `httpx.Client()` with default `trust_env` so the
  MCP client would pick up the sandbox egress proxy automatically.
- **Expected:** the client routes through `HTTPS_PROXY` and works.
- **Actual:** `httpx.InvalidURL: Invalid port: ':1]'` at *construction* —
  the sandbox `NO_PROXY` contains bracketed IPv6 literals
  (`[fd8b:4f84:7d32:99::1]`) that break httpx's no-proxy URLPattern parser.
  Separately verified that direct egress is blocked here (TLS handshake
  timeout), so the proxy is mandatory, not optional.
- **Severity:** medium — every endpoint would 500 in this environment.
- **Workaround:** `backend/mcp_client.py` now reads `HTTPS_PROXY` /
  `ALL_PROXY` itself and passes `proxy=` explicitly with `trust_env=False`.
  No-proxy bypass lists are irrelevant: the only host ever called is the
  public Mermail endpoint. Standard behavior on judges' machines (proxy
  when set, direct otherwise).
- **Suggestion:** httpx should tolerate bracketed IPv6 literals in
  `NO_PROXY` instead of raising at client construction.

## 5. `notifications/initialized` empty body crashed our own client — 2026-09-29

- **Tried:** first live authenticated session after minting a real API key —
  `initialize` → `notifications/initialized` → `tools/list` against the
  hosted MCP server.
- **Expected:** the handshake completes; the key-gated test goes green.
- **Actual:** `initialize` succeeded, then our client raised
  `MCPProtocolError: non-SSE, non-JSON response: ''` on the
  `notifications/initialized` POST. The server answers that notification
  with HTTP 200 and an empty body — correct per the MCP Streamable HTTP
  spec (notifications get no response) — but our `_post()` treated every
  empty body as a protocol violation. This bug was latent since commit one:
  the key-gated test had been skipped for lack of a key, so nothing had
  ever exercised the notification step against the real server.
- **Severity:** high for us (the hard-gate path was broken end to end),
  low as a platform issue (the server behaved correctly).
- **Workaround:** `_post()` gained an `allow_empty` flag; `connect()` sends
  `notifications/initialized` with it. Fix verified: 28/28 tests pass and
  `/api/ask` answers "what is unpaid?" against the live inbox.
- **Suggestion:** none for Mermail — the lesson is ours: never ship a
  protocol client whose live path was never run. A skipped key-gated test
  is a known-unknown, not a passing test.

## 6. `search_emails` free-text field IS `query` — closing entry 3 — 2026-09-29

- **Tried:** `describe_tool("search_emails")` against the live server to
  resolve entry 3's open question.
- **Expected:** one of several plausible names (`search`, `q`, `text`).
- **Actual:** the query object's free-text field is named `query` — i.e.
  `{"mailboxId": id, "query": {"query": "<free text>", "folder": "inbox",
  "limit": 25}}`. Our default (`"search"`) was wrong and would have
  searched unfiltered at demo time. Also confirmed: `sortColumn`,
  `sortDirection`, and `agent_safe_content` are NOT valid
  `search_emails` query fields (valid set: query/from/to/subject/
  date_start/date_end/folder/is_read/is_starred/category/has_attachment/
  require_scan_status/include_held/metadata_only/page/limit) — dropped
  from our call. `get_email`'s `query` object DOES accept
  `require_scan_status` and `agent_safe_content`, so that call was already
  correct.
- **Severity:** medium — silent wrong-field failures are demo killers.
- **Workaround:** default `SEARCH_TEXT_KEY` is now `"query"`
  (`MERMAIL_SEARCH_KEY` override retained); the app's `search_emails`
  payload only sends schema-valid fields.
- **Suggestion:** same as entry 3 — Mermail docs should name the free-text
  field; the example in the skill reference searches without any text at
  all, which is how this went unnoticed.
