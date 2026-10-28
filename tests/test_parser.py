from decimal import Decimal

import pytest

from bolkhata.transaction_parser import ParseError, parse_transaction


def test_parses_hindi_credit():
    parsed = parse_transaction("रमेश के खाते में 70 रुपये उधार लिख दो।")
    assert parsed.customer_name == "रमेश"
    assert parsed.amount == Decimal("70")
    assert parsed.transaction_type == "credit"


def test_parses_repayment():
    parsed = parse_transaction("मीना ने 50 रुपये वापस दिए।")
    assert parsed.customer_name == "मीना"
    assert parsed.amount == Decimal("50")
    assert parsed.transaction_type == "repayment"


def test_parses_hindi_amount_word():
    assert parse_transaction("रमेश के खाते में सत्तर रुपये उधार लिख दो।").amount == Decimal("70")


@pytest.mark.parametrize("text", ["रमेश के खाते में उधार लिख दो।", "रमेश के खाते में 0 रुपये उधार लिख दो।", "70 रुपये उधार लिख दो।"])
def test_rejects_unsafe_or_incomplete_transcript(text):
    with pytest.raises(ParseError):
        parse_transaction(text)


def test_rejects_ambiguous_type():
    with pytest.raises(ParseError):
        parse_transaction("रमेश के खाते में 70 रुपये दर्ज करो।")
