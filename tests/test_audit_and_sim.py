"""Tests for backend/audit.py and backend/alexa_sim.py."""

from backend.alexa_sim import (
    INTENT_AUDIT,
    INTENT_DUPLICATES,
    INTENT_FALLBACK,
    INTENT_HELP,
    INTENT_SINGLE,
    INTENT_UNPAID,
    build_single_response,
    build_unpaid_response,
    respond,
    route,
    strip_wake_word,
)
from backend.audit import (
    InvoiceClaim,
    extract_claim,
    reconcile,
    summarize,
)


def _email(**kw):
    base = {
        "id": "e1",
        "subject": "Invoice INV-2043 due",
        "from": "billing@ghostware.dev",
        "date": "2026-09-20",
        "body": "Please remit 2,000.00 USDC for INV-2043 by 2026-09-30. Thanks!",
    }
    base.update(kw)
    return base


# -- extraction ------------------------------------------------------------


def test_extract_invoice_id_amount_asset():
    claim = extract_claim(_email())
    assert claim.invoice_id == "INV-2043"
    assert claim.amount == 2000.00
    assert claim.asset == "USDC"
    assert claim.date == "2026-09-20"


def test_extract_currency_first_form():
    claim = extract_claim(_email(body="USDC 840.50 for invoice INV-2042"))
    assert claim.amount == 840.50
    assert claim.asset == "USDC"


def test_extract_missing_fields_degrade_to_none():
    claim = extract_claim({"id": "x", "subject": "hi", "body": "no numbers here"})
    assert claim.invoice_id is None
    assert claim.amount is None


# -- reconciliation --------------------------------------------------------


def test_reconcile_marks_unpaid_with_honest_evidence():
    claim = extract_claim(_email())
    (verdict,) = reconcile([claim])
    assert verdict.verdict == "UNPAID"
    assert "unverifiable" in verdict.evidence.lower()


def test_reconcile_flags_duplicate_claims():
    c1 = extract_claim(_email())
    c2 = extract_claim(_email(id="e2", subject="REMINDER: Invoice INV-2043 due"))
    verdicts = reconcile([c1, c2])
    assert verdicts[0].verdict == "UNPAID"
    assert verdicts[1].verdict == "DUPLICATE_CLAIM"


def test_reconcile_ignores_non_invoice_mail():
    verdicts = reconcile([extract_claim({"id": "x", "subject": "hi", "body": "no numbers"})])
    assert verdicts == []


def test_summarize_totals():
    claims = [
        extract_claim(_email()),
        extract_claim(_email(id="e2", subject="Invoice INV-2041", body="1,250.00 USDC INV-2041")),
    ]
    summary = summarize(reconcile(claims))
    assert summary["invoices"] == 2
    assert summary["total_claimed"] == 3250.00
    assert summary["by_verdict"]["UNPAID"] == 2


# -- interaction model -----------------------------------------------------


def test_strip_wake_word():
    assert strip_wake_word("Alexa, what's unpaid?") == "what's unpaid?"
    assert strip_wake_word("hey alexa audit my invoices") == "audit my invoices"
    assert strip_wake_word("what's unpaid") == "what's unpaid"


def test_route_intents():
    assert route("Alexa, what's unpaid?")[0] == INTENT_UNPAID
    assert route("what do I owe")[0] == INTENT_UNPAID
    assert route("audit this month's invoices")[0] == INTENT_AUDIT
    assert route("Alexa, is invoice INV-2044 paid?")[0] == INTENT_SINGLE
    assert route("any duplicate invoices?")[0] == INTENT_DUPLICATES
    assert route("help")[0] == INTENT_HELP
    assert route("tell me a joke")[0] == INTENT_FALLBACK


def test_single_invoice_slot_extraction():
    intent, slots = route("check INV-2041 please")
    assert intent == INTENT_SINGLE
    assert slots["invoice_id"] == "INV-2041"


def _claims():
    return [extract_claim(_email()), extract_claim(_email(id="e2", subject="Invoice INV-2041", body="1,250.00 USDC INV-2041", **{"from": "billing@acmelabs.io"}))]


def test_unpaid_response_speech_and_cards():
    verdicts = reconcile(_claims())
    resp = build_unpaid_response(verdicts, summarize(verdicts))
    assert resp["intent"] == INTENT_UNPAID
    assert "3,250.00" in resp["speech"]
    assert len(resp["cards"]) == 2
    assert all(c["verdict"] == "UNPAID" for c in resp["cards"])


def test_single_response_found_and_missing():
    verdicts = reconcile(_claims())
    found = build_single_response(verdicts, summarize(verdicts), "INV-2043")
    assert "unpaid" in found["speech"].lower()
    missing = build_single_response(verdicts, summarize(verdicts), "INV-9999")
    assert "couldn't find" in missing["speech"]
    assert missing["cards"] == []


def test_respond_dispatches():
    resp = respond(INTENT_AUDIT, {}, _claims())
    assert resp["intent"] == INTENT_AUDIT
    assert resp["speech"].startswith("Audit complete")
