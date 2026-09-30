# Alexa+ Invoice Radar — "Alexa, what's unpaid?"

**The problem:** a freelancer or small-biz owner gets paid in crypto (USDC)
and their invoices arrive as *email*. Answering "what's unpaid right now?"
means 20 minutes of inbox archaeology — search, open, copy amounts into a
spreadsheet, remember what you already paid. The current workaround is a
spreadsheet plus memory, and memory loses.

**This entry:** a simulated Alexa+ voice experience in a web app. You say
*"Alexa, what's unpaid?"* and it searches your real inbox over the Mermail
MCP server, reads the invoice emails, extracts invoice IDs and amounts,
reconciles them, and speaks the answer back — with invoice cards on screen.
One workflow, end to end, voice in → real data → voice out.

Built for the **Amazon Developer Hackathon, Alexa+ track** (simulated
Alexa+ experience path).

---

## 30-second demo

1. Open the app. The status pill reads **MCP connected · 12 tools · agent-inbox**.
2. Hold the mic button (or type) and ask: **"Alexa, what's unpaid?"**
3. Alexa+ searches the last 30 days of inbox mail via `search_emails`,
   reads each invoice email via `get_email`, extracts claims, reconciles.
4. Spoken answer: *"I found 4 invoices claiming 4,590.50 USDC. 4 are unpaid,
   totaling 4,590.50 USDC. The largest is INV-2043 for 2,000.00 USDC from
   Ghostware. I can't verify payments against your wallet from here, so I'm
   reporting everything as unpaid rather than guessing."*
5. Invoice cards render below with verdict badges. Try also:
   - *"Alexa, audit this month's invoices"* → full audit
   - *"Alexa, is invoice INV-2044 paid?"* → single-invoice check
   - *"Alexa, check for duplicate invoices"* → duplicate sweep

## Run it (2 minutes)

```bash
git clone https://github.com/knowaguy4u-cell/alexa-plus-invoice-radar.git
cd alexa-plus-invoice-radar
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Get a Mermail project API key: console.mermail.app → Settings → API Keys
export MERMAIL_API_KEY=sk-proj-...
# export MERMAIL_MAILBOX=you@mermail.app   # optional; default nova-demo@mermail.app

uvicorn backend.main:app --port 8000
# open http://localhost:8000
```

Without `MERMAIL_API_KEY` the UI loads but `/api/ask` refuses with a clear
503 — the sim **never fabricates inbox data**. `GET /api/health` reports
`not_configured` / `connected` / `auth_error` so you always know the MCP
state. `GET /api/tools` dumps the live tool catalog (schema self-check).

Run the tests: `pytest -q` (includes a live probe of the real MCP endpoint's
auth boundary — no key needed).

## Architecture

```
browser (voice in/out, Web Speech API) ──/api/ask──▶ FastAPI (backend/main.py)
        ▲ transcript + invoice cards                         │
        │ speechSynthesis                                    ▼
        │                                         alexa_sim.py — intent router
        │                                         (deterministic regex, no LLM)
        │                                                    │
        │                                                    ▼
        │                                         audit.py — extract claims,
        │                                         reconcile: UNPAID / DUPLICATE_CLAIM
        │                                                    │
        └───────────────────────────────────────── mcp_client.py ◀── HARD GATE
                                                          MCP Streamable HTTP
                                                          (initialize → tools/list
                                                           → tools/call, SSE)
                                                          https://console.mermail.app/mcp
                                                          ?profile=agent-inbox (12 tools)
```

**The hard gate:** `backend/mcp_client.py` is the track's required-technology
integration *in code* — a hand-rolled MCP Streamable-HTTP client (`httpx`,
zero new deps) that performs the real `initialize` handshake, `tools/list`,
and `tools/call` against the hosted Mermail MCP server. Every inbox read in
this repo goes through it. `GET /api/health` proves it live.

**Honesty rule (by design):** this app's MCP connection uses API-key mode,
and API keys never unlock Agent Wallet / PayBox tools — so payment history
is *unverifiable* here. Every claim is reported **UNPAID** with the evidence
stated, never fabricated as paid. Duplicate detection *is* computable from
email alone and is performed. This mirrors the live demo of the underlying
skill and is the only non-theatrical behavior available.

## What was built when

- **During the submission window (new work):** the simulated Alexa+
  interaction model (`alexa_sim.py`), the voice-first web app
  (`frontend/index.html`), the reconciliation engine (`audit.py`), the
  FastAPI wiring, the tests, this README, the friction log.
- **Pre-existing (declared, not claimed):** the Mermail inbox MCP server
  and the mermail-invoice-audit agent skill's reconciliation rules, which
  this project drives as a client.

## Friction log

[FRICTION_LOG.md](FRICTION_LOG.md) — every wall hit during the build,
with severity, workaround, and an actionable suggestion for the platform
owner. Started at commit one.

## License

MIT — [LICENSE](LICENSE).
