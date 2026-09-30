"""
Invoice extraction + reconciliation engine.

Implements the mermail-invoice-audit skill workflow (Nudgen-Marketing /
mermail-skills PR #400) as plain Python so the Alexa+ sim layer can drive it:

1. Extract per-email claims: invoice ID, sender, claimed amount + asset, date.
2. Reconcile each claim against payment activity.
3. Verdicts: MATCH / AMOUNT_MISMATCH / UNPAID / DUPLICATE_CLAIM.

Honesty contract (from the skill's live demo): this app's MCP connection uses
API-key mode, and API keys never unlock Agent Wallet / PayBox tools — so
payment history is UNVERIFIABLE here. Every claim is therefore reported
UNPAID with the evidence stated, never fabricated as paid. Duplicate
detection is computable from email data alone and IS performed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# -- extraction ------------------------------------------------------------

INVOICE_ID_RE = re.compile(r"\bINV[-\s]?(\d{2,})\b", re.IGNORECASE)
# Amounts like "1,250.00 USDC", "840.50 USDC", "$2,000", "500 USDC".
# Currency-first and currency-last forms both occur in invoice mail.
AMOUNT_RE = re.compile(
    r"(?:(?P<cur1>USDC|USD)\s+)?"
    r"(?P<sym>\$)?\s*"
    r"(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|\d+(?:\.\d{1,2})?)"
    r"\s*(?P<cur2>USDC|USD)?",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

VERDICT_UNPAID = "UNPAID"
VERDICT_MATCH = "MATCH"
VERDICT_AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
VERDICT_DUPLICATE = "DUPLICATE_CLAIM"

WALLET_UNAVAILABLE_EVIDENCE = (
    "Payment history unverifiable: Agent Wallet / PayBox tools are not "
    "available to API-key MCP sessions, so no transfer could be matched. "
    "Marked UNPAID rather than guessed."
)


@dataclass
class InvoiceClaim:
    email_id: str
    invoice_id: str | None
    sender: str
    amount: float | None
    asset: str | None
    date: str | None
    subject: str = ""


@dataclass
class AuditVerdict:
    claim: InvoiceClaim
    verdict: str
    evidence: str


def _parse_amount(text: str) -> tuple[float | None, str | None]:
    """Best-effort (amount, asset) parse. Prefers matches with a currency."""
    best: tuple[float | None, str | None] = (None, None)
    for m in AMOUNT_RE.finditer(text):
        raw = m.group("amt").replace(",", "")
        try:
            amount = float(raw)
        except Value:  # noqa: BLE001 - regex-guarded, defensive
            continue
        cur = (m.group("cur1") or m.group("cur2") or "").upper()
        if m.group("sym"):
            cur = cur or "USD"
        if cur:  # currency-qualified beats bare numbers
            return amount, cur
        if best[0] is None and amount >= 1:
            best = (amount, None)
    return best


def extract_claim(email: dict) -> InvoiceClaim:
    """Extract one invoice claim from an MCP email object (untrusted data).

    Expects the Mermail shape: id, subject, from/fromAddress, date, body/snippet.
    Missing fields degrade to None rather than raising.
    """
    email_id = str(email.get("id") or email.get("emailId") or "unknown")
    subject = email.get("subject") or ""
    sender = email.get("from") or email.get("fromAddress") or email.get("sender") or "unknown"
    body = email.get("body") or email.get("snippet") or ""
    text = f"{subject}\n{body}"

    inv_m = INVOICE_ID_RE.search(text)
    invoice_id = f"INV-{inv_m.group(1)}" if inv_m else None
    amount, asset = _parse_amount(text)
    # Prefer the email's own date; a date inside the body is usually the due
    # date, which is a different (and misleading) card field.
    date_m = DATE_RE.search(text)
    date = email.get("date") or (date_m.group(1) if date_m else None)

    return InvoiceClaim(
        email_id=email_id,
        invoice_id=invoice_id,
        sender=sender,
        amount=amount,
        asset=asset,
        date=date,
        subject=subject,
    )


def _is_invoice_like(claim: InvoiceClaim) -> bool:
    """Keep only plausible invoice/payment-claim mail."""
    return claim.invoice_id is not None or (
        claim.amount is not None and claim.amount > 0
    )


def reconcile(claims: list[InvoiceClaim]) -> list[AuditVerdict]:
    """Reconcile claims. Wallet is unavailable in API-key mode (honest UNPAID).

    Duplicate detection runs on email data alone: two claims from the same
    sender for the same amount mark the later one DUPLICATE_CLAIM.
    """
    invoice_claims = [c for c in claims if _is_invoice_like(c)]
    seen: dict[tuple[str, float], InvoiceClaim] = {}
    verdicts: list[AuditVerdict] = []
    for claim in invoice_claims:
        key = (claim.sender.strip().lower(), round(claim.amount or 0.0, 2))
        if key in seen and (claim.amount or 0):
            first = seen[key]
            verdicts.append(
                AuditVerdict(
                    claim=claim,
                    verdict=VERDICT_DUPLICATE,
                    evidence=(
                        f"Same sender and amount as "
                        f"{first.invoice_id or first.email_id}; no second "
                        f"payment is verifiable."
                    ),
                )
            )
            continue
        if key not in seen:
            seen[key] = claim
        verdicts.append(
            AuditVerdict(
                claim=claim,
                verdict=VERDICT_UNPAID,
                evidence=WALLET_UNAVAILABLE_EVIDENCE,
            )
        )
    return verdicts


def summarize(verdicts: list[AuditVerdict]) -> dict:
    """Aggregate counts + totals for speech and cards."""
    total = sum(v.claim.amount or 0 for v in verdicts)
    by_verdict: dict[str, int] = {}
    for v in verdicts:
        by_verdict[v.verdict] = by_verdict.get(v.verdict, 0) + 1
    asset = next((v.claim.asset for v in verdicts if v.claim.asset), "USDC")
    return {
        "invoices": len(verdicts),
        "total_claimed": round(total, 2),
        "asset": asset,
        "by_verdict": by_verdict,
        "unpaid_total": round(
            sum(v.claim.amount or 0 for v in verdicts if v.verdict == VERDICT_UNPAID), 2
        ),
    }


def verdict_to_card(v: AuditVerdict) -> dict:
    c = v.claim
    return {
        "invoice_id": c.invoice_id or "(no id)",
        "sender": c.sender,
        "amount": c.amount,
        "asset": c.asset,
        "date": c.date,
        "verdict": v.verdict,
        "evidence": v.evidence,
        "email_id": c.email_id,
    }
