"""
Simulated Alexa+ interaction model.

This is the NEW work of the submission window: a voice-first conversational
layer that maps spoken utterances to the invoice-audit workflow, the way an
Alexa+ skill's interaction model maps sample utterances to intents. It is a
*simulation* of the Alexa+ experience (per the track's blessed
"simulated Alexa+ experience in a web app" path) — deterministic,
keyword/regex-routed, no LLM, $0.

Intents:
    unpaid_invoices   "what's unpaid?", "what do I owe", "unpaid invoices"
    full_audit        "audit this month's invoices", "run an invoice audit"
    single_invoice    "is invoice INV-2044 paid?", "check INV-2041"
    duplicate_sweep   "any duplicate invoices?", "check for duplicates"
    help              "help", "what can you do"
    fallback          anything else -> clarifying prompt (never a guess)
"""

from __future__ import annotations

import re

from .audit import (
    VERDICT_DUPLICATE,
    VERDICT_UNPAID,
    InvoiceClaim,
    reconcile,
    summarize,
    verdict_to_card,
)

INTENT_UNPAID = "unpaid_invoices"
INTENT_AUDIT = "full_audit"
INTENT_SINGLE = "single_invoice"
INTENT_DUPLICATES = "duplicate_sweep"
INTENT_HELP = "help"
INTENT_FALLBACK = "fallback"

_WAKE = re.compile(r"^\s*(hey\s+)?alexa[,.]?\s+", re.IGNORECASE)
_INV_ID = re.compile(r"INV[-\s]?(\d{2,})", re.IGNORECASE)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    (INTENT_HELP, re.compile(r"\bhelp\b|\bwhat can you do\b", re.I)),
    (INTENT_DUPLICATES, re.compile(r"duplicat", re.I)),
    (
        INTENT_SINGLE,
        re.compile(r"\b(is|was)\b.*\binvoice\b.*\bpaid\b|\bcheck\b.*\bINV", re.I),
    ),
    (
        INTENT_AUDIT,
        re.compile(r"\baudit\b.*\binvoice|\binvoices\b.*\baudit\b|\bfull audit\b", re.I),
    ),
    (
        INTENT_UNPAID,
        re.compile(r"\bunpaid\b|\bwhat do i owe\b|\bowe\b|\boutstanding\b|\bnot paid\b", re.I),
    ),
]


def strip_wake_word(text: str) -> str:
    return _WAKE.sub("", text).strip()


def route(utterance: str) -> tuple[str, dict]:
    """Map a (wake-word-stripped) utterance to (intent, slots)."""
    text = strip_wake_word(utterance)
    for intent, pattern in _PATTERNS:
        if pattern.search(text):
            slots: dict = {}
            m = _INV_ID.search(text)
            if m:
                slots["invoice_id"] = f"INV-{m.group(1)}"
            return intent, slots
    return INTENT_FALLBACK, {}


def _fmt_money(amount: float | None, asset: str | None) -> str:
    if amount is None:
        return "an unknown amount"
    return f"{amount:,.2f} {asset or 'USDC'}"


def build_unpaid_response(verdicts, summary) -> dict:
    cards = [verdict_to_card(v) for v in verdicts]
    n = summary["invoices"]
    if n == 0:
        speech = (
            "I searched your inbox for invoices from the last 30 days and found "
            "none. Nothing is unpaid that I can see."
        )
    else:
        unpaid = summary["by_verdict"].get(VERDICT_UNPAID, 0)
        dupes = summary["by_verdict"].get(VERDICT_DUPLICATE, 0)
        biggest = max(verdicts, key=lambda v: v.claim.amount or 0)
        speech = (
            f"I found {n} invoice{'s' if n != 1 else ''} claiming "
            f"{_fmt_money(summary['total_claimed'], summary['asset'])}. "
            f"{unpaid} {'is' if unpaid == 1 else 'are'} unpaid, totaling "
            f"{_fmt_money(summary['unpaid_total'], summary['asset'])}. "
            f"The largest is {biggest.claim.invoice_id or 'an invoice'} for "
            f"{_fmt_money(biggest.claim.amount, biggest.claim.asset)} from "
            f"{biggest.claim.sender}. "
            f"I can't verify payments against your wallet from here, so I'm "
            f"reporting everything as unpaid rather than guessing."
        )
        if dupes:
            speech += f" I also flagged {dupes} possible duplicate claim{'s' if dupes != 1 else ''}."
    return {"intent": INTENT_UNPAID, "speech": speech, "cards": cards, "summary": summary}


def build_audit_response(verdicts, summary) -> dict:
    base = build_unpaid_response(verdicts, summary)
    base["intent"] = INTENT_AUDIT
    n = summary["invoices"]
    flagged = sum(
        c for v, c in summary["by_verdict"].items() if v != "MATCH"
    )
    base["speech"] = (
        f"Audit complete. {n} invoice{'s' if n != 1 else ''} reviewed, "
        f"{flagged} flagged for your review. {base['speech']}"
    )
    return base


def build_single_response(verdicts, summary, invoice_id: str | None) -> dict:
    wanted = (invoice_id or "").upper()
    match = next(
        (v for v in verdicts if (v.claim.invoice_id or "").upper() == wanted), None
    )
    if match and wanted:
        c = match.claim
        speech = (
            f"{wanted}, {_fmt_money(c.amount, c.asset)} from {c.sender}, "
            f"is {match.verdict.lower().replace('_', ' ')}. {match.evidence}"
        )
        cards = [verdict_to_card(match)]
    else:
        speech = (
            f"I couldn't find {wanted or 'that invoice'} in the last 30 days "
            f"of inbox mail. Try 'what's unpaid' to hear everything I found."
        )
        cards = []
    return {"intent": INTENT_SINGLE, "speech": speech, "cards": cards, "summary": summary}


def build_duplicates_response(verdicts, summary) -> dict:
    dupes = [v for v in verdicts if v.verdict == VERDICT_DUPLICATE]
    cards = [verdict_to_card(v) for v in dupes]
    if dupes:
        speech = (
            f"I found {len(dupes)} possible duplicate claim{'s' if len(dupes) != 1 else ''}: "
            + "; ".join(
                f"{v.claim.invoice_id or 'an invoice'} for "
                f"{_fmt_money(v.claim.amount, v.claim.asset)} from {v.claim.sender}"
                for v in dupes
            )
            + ". The later claims are flagged — no second payment is verifiable."
        )
    else:
        speech = "No duplicate invoice claims in the last 30 days. Every claim appears once."
    return {"intent": INTENT_DUPLICATES, "speech": speech, "cards": cards, "summary": summary}


def build_help_response() -> dict:
    speech = (
        "I'm Invoice Radar, a simulated Alexa+ skill. Ask me 'what's unpaid', "
        "'audit this month's invoices', 'is invoice INV-2044 paid', or "
        "'check for duplicate invoices'."
    )
    return {"intent": INTENT_HELP, "speech": speech, "cards": [], "summary": {}}


def build_fallback_response() -> dict:
    speech = (
        "I didn't catch that as an invoice request. Try 'what's unpaid', "
        "'audit my invoices', or 'is invoice INV-2044 paid'."
    )
    return {"intent": INTENT_FALLBACK, "speech": speech, "cards": [], "summary": {}}


def respond(intent: str, slots: dict, claims: list[InvoiceClaim]) -> dict:
    """Build the full voice+visual response for an intent over live claims."""
    verdicts = reconcile(claims)
    summary = summarize(verdicts)
    if intent == INTENT_UNPAID:
        return build_unpaid_response(verdicts, summary)
    if intent == INTENT_AUDIT:
        return build_audit_response(verdicts, summary)
    if intent == INTENT_SINGLE:
        return build_single_response(verdicts, summary, slots.get("invoice_id"))
    if intent == INTENT_DUPLICATES:
        return build_duplicates_response(verdicts, summary)
    if intent == INTENT_HELP:
        return build_help_response()
    return build_fallback_response()
