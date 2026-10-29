from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal


class ParseError(ValueError):
    """Raised when a transcript is not safe to turn into a transaction."""


@dataclass(frozen=True)
class ParsedTransaction:
    customer_name: str
    amount: Decimal
    transaction_type: str
    description: str


NUMBER_WORDS = {
    "एक": 1, "एक्क": 1, "one": 1,
    "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
    "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15,
    "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
    "तीस": 30, "चालीस": 40, "पचास": 50, "पचास": 50, "साठ": 60,
    "सत्तर": 70, "अस्सी": 80, "नब्बे": 90, "सौ": 100,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "paanch": 5,
    "das": 10, "bees": 20, "tees": 30, "pachaas": 50, "saath": 60,
    "sattar": 70, "assi": 80, "nabbe": 90, "sau": 100,
    "एकशे": 100, "पन्नास": 50, "सत्तर": 70, "शंभर": 100,
}
AMOUNT_UNITS = {"रुपये", "रुपया", "रुपए", "रुपय", "rs", "inr", "rupees", "rupee", "rupaye", "rupay"}
REPAYMENT_WORDS = ("वापस", "जमा", "चुकता", "भुगतान", "दिए", "दिये", "दिया", "returned", "paid", "jama", "wapas",
                   "diye", "diya", "chukta", "chukaye")
CREDIT_WORDS = ("उधार", "लिख", "देना", "लेना", "credit", "borrow", "udhaar", "udhar", "likh")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _extract_amount(text: str) -> Decimal:
    numeric = re.search(r"(?<!\w)(\d+(?:[.,]\d{1,2})?)(?:\s*(?:रुपये?|रुपए|rs\.?|inr|rupees?))?", text, re.IGNORECASE)
    if numeric:
        return Decimal(numeric.group(1).replace(",", "."))
    tokens = re.findall(r"[\w\u0900-\u097F]+", text.lower())
    for index, token in enumerate(tokens):
        if token in NUMBER_WORDS:
            # Roman words such as "do" (2) are everyday verbs ("likh do"); count them only before a currency unit.
            if token.isascii() and not (index + 1 < len(tokens) and tokens[index + 1] in AMOUNT_UNITS):
                continue
            value = NUMBER_WORDS[token]
            if index + 1 < len(tokens) and tokens[index + 1] in NUMBER_WORDS and value < 100:
                value += NUMBER_WORDS[tokens[index + 1]]
            return Decimal(value)
    raise ParseError("I could not find a positive amount.")


def _extract_customer(text: str) -> str:
    patterns = (
        r"^\s*(.+?)\s+के\s+खाते",
        r"^\s*(.+?)\s+ने\s+.+?(?:वापस|जमा|दिए|दिये|दिया)",
        r"^\s*(.+?)\s+ke\s+(?:khate|khaate|account)",
        r"^\s*(.+?)\s+ne\s+.+?(?:wapas|jama|diye|diya|chukta|chukaye|paid|returned)",
        r"^\s*(.+?)\s+(?:account|ke account)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = re.sub(r"^(?:customer|ग्राहक)\s+", "", match.group(1), flags=re.IGNORECASE).strip(" ,।")
            if name:
                return name
    raise ParseError("I could not identify the customer name.")


def parse_transaction(text: str) -> ParsedTransaction:
    clean = _clean(text)
    if not clean:
        raise ParseError("Transcript is empty.")
    lowered = clean.lower()
    has_repayment = any(word in lowered for word in REPAYMENT_WORDS)
    has_credit = any(word in lowered for word in CREDIT_WORDS)
    if has_repayment == has_credit:
        raise ParseError("Please make the transaction type clear: credit or repayment.")
    amount = _extract_amount(clean)
    if amount <= 0:
        raise ParseError("Amount must be greater than zero.")
    customer = _extract_customer(clean)
    description = re.sub(r"^\s*.*?(?:खाते|khate|khaate|account)", "", clean, count=1, flags=re.IGNORECASE).strip(" .,।")
    return ParsedTransaction(customer, amount, "repayment" if has_repayment else "credit", description)
