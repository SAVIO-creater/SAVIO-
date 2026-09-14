"""
SAVIO Brain — typo-tolerant understanding + exact financial math.

This module sits in front of Gemini. It:
  1. Normalizes messy questions (missing letters, missing words, slang).
  2. Scores intents even when the sentence is incomplete.
  3. Resolves member names with fuzzy + phonetic matching.
  4. Computes every money figure in Python so the model cannot invent math.

Why this is not 3,000 handwritten misspellings
----------------------------------------------
A single 6-letter word has hundreds of 1-edit neighbours. Writing them by
hand is brittle. Instead we:
  * keep a compact lexicon of real savings-group words and phrases
  * generate deletion / swap / keyboard-neighbour variants at load time
  * score a question by how many intent keywords survive after fuzzy match
That covers "savngs", "how mch john save", "totl loan", "who nt deposited".
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

try:
    import psycopg2
    from psycopg2.extras import DictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    DictCursor = None
    PSYCOPG2_AVAILABLE = False


# ---------------------------------------------------------------------------
# Keyboard / edit-distance primitives
# ---------------------------------------------------------------------------

QWERTY_NEIGHBORS = {
    "a": "qwsz",
    "b": "vghn",
    "c": "xdfv",
    "d": "erfcxs",
    "e": "rdsw",
    "f": "rtgvcd",
    "g": "tyhbvf",
    "h": "yujnbg",
    "i": "ujko",
    "j": "uiknhm",
    "k": "ioljm",
    "l": "kop",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "awedxz",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
}

VOWELS = set("aeiou")


def fold(text: str) -> str:
    """Lowercase, strip accents, keep letters/digits/spaces only."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = text.replace("&", " and ").replace("+", " plus ").replace("%", " percent ")
    text = text.replace("/", " ").replace("-", " ").replace("_", " ")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokens(text: str) -> list[str]:
    return [t for t in fold(text).split() if t]


def levenshtein(a: str, b: str, limit: int = 3) -> int:
    """Edit distance with an early exit when worse than `limit`."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            ins = current[j - 1] + 1
            delete = previous[j] + 1
            sub = previous[j - 1] + (ca != cb)
            val = ins if ins < delete else delete
            if sub < val:
                val = sub
            current.append(val)
            if val < row_min:
                row_min = val
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def similarity(a: str, b: str) -> float:
    """0..1 similarity from edit distance. Short tokens must be almost exact."""
    a, b = fold(a), fold(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter in longer and len(shorter) >= 4 and len(shorter) / len(longer) >= 0.7:
        return 0.8 + 0.2 * (len(shorter) / len(longer))
    if abs(len(a) - len(b)) > max(2, len(longer) // 3):
        return 0.0
    limit = 1 if min(len(a), len(b)) <= 4 else 2 if min(len(a), len(b)) <= 7 else 3
    dist = levenshtein(a, b, limit=limit)
    if dist > limit:
        return 0.0
    return max(0.0, 1.0 - dist / len(longer))


def soundex(word: str) -> str:
    word = fold(word)
    if not word:
        return ""
    first = word[0].upper()
    mapping = str.maketrans(
        "bfpvcgjkqsxzdtlmnr",
        "111122222222334556",
    )
    encoded = word.translate(mapping)
    compact = first
    prev = mapping.get(word[0], "") if False else encoded[0] if encoded else ""
    # rebuild using classic rules
    codes = {
        "b": "1", "f": "1", "p": "1", "v": "1",
        "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
        "d": "3", "t": "3",
        "l": "4",
        "m": "5", "n": "5",
        "r": "6",
    }
    compact = word[0].upper()
    prev_code = codes.get(word[0], "")
    for ch in word[1:]:
        code = codes.get(ch, "")
        if code and code != prev_code:
            compact += code
        prev_code = code if code else prev_code if ch not in VOWELS and ch != "h" and ch != "w" else ""
        if len(compact) == 4:
            break
    return (compact + "000")[:4]


def metaphone_lite(word: str) -> str:
    """Tiny phonetic key: drop vowels except leading, collapse doubles."""
    w = fold(word)
    if not w:
        return ""
    out = [w[0]]
    prev = w[0]
    for ch in w[1:]:
        if ch in VOWELS:
            continue
        if ch == prev:
            continue
        out.append(ch)
        prev = ch
    return "".join(out)


def missing_letter_variants(word: str) -> set[str]:
    """Every way to drop one letter — covers 'savngs', 'depost', 'intrst'."""
    word = fold(word)
    out = set()
    if len(word) < 3:
        return out
    for i in range(len(word)):
        out.add(word[:i] + word[i + 1:])
    return out


def swapped_variants(word: str) -> set[str]:
    word = fold(word)
    out = set()
    for i in range(len(word) - 1):
        out.add(word[:i] + word[i + 1] + word[i] + word[i + 2:])
    return out


def neighbor_variants(word: str) -> set[str]:
    word = fold(word)
    out = set()
    for i, ch in enumerate(word):
        for n in QWERTY_NEIGHBORS.get(ch, ""):
            out.add(word[:i] + n + word[i + 1:])
    return out


def extra_letter_variants(word: str) -> set[str]:
    """Common double-letter slips: 'saavings', 'loann'."""
    word = fold(word)
    out = set()
    for i, ch in enumerate(word):
        out.add(word[:i] + ch + word[i:])
    return out


def vowel_drop_variant(word: str) -> str:
    """Consonant skeleton: savings -> svngs, deposit -> dpst."""
    w = fold(word)
    if not w:
        return ""
    core = w[0] + "".join(ch for ch in w[1:] if ch not in VOWELS)
    return core


# ---------------------------------------------------------------------------
# Lexicon — savings-group language, slang, and common misspellings
# ---------------------------------------------------------------------------

# Canonical word -> list of human aliases (typos are generated on top).
CANONICAL_WORDS: dict[str, list[str]] = {
    "savings": [
        "saving", "save", "saved", "saves", "saver", "savers",
        "deposit", "deposite", "deposited", "deposits", "deposited",
        "contribution", "contribute", "contributed", "contrib",
        "shareout", "share out",
        "sent", "sente", "money in", "put in", "pay in",
        "collection", "collected", "collections",
    ],
    "loan": [
        "loans", "lend", "lending", "borrow", "borrowed", "borrowing",
        "advance", "advances", "credit", "owing", "owes", "owed", "debt",
        "debts", "due", "outstanding loan",
    ],
    "interest": [
        "interests", "intrest", "intreast", "rate", "rates", "percent",
        "percentage", "charge", "charges", "profit on loan",
    ],
    "welfare": [
        "welfar", "welbeing", "wellbeing", "social fund", "social",
        "emergency fund", " benevolence", "help fund",
    ],
    "fine": [
        "fines", "penalty", "penalties", "punish", "punishment",
        "late fee", "latefee", "surcharge",
    ],
    "payment": [
        "payments", "repay", "repayment", "repayments", "payback",
        "pay back", "settle", "settled", "clear", "cleared", "installment",
        "instalment", "installments", "pay", "pays", "paid", "paying",
    ],
    "balance": [
        "balances", "remaining", "left", "available", "cash", "cash at hand",
        "on hand", "treasury", "float", "net", "net cash",
    ],
    "profit": [
        "profits", "surplus", "gain", "gains", "income", "earnings",
        "dividend", "dividends", "return",
    ],
    "member": [
        "members", "person", "people", "client", "clients", "name",
        "names", "account", "accounts", "who",
    ],
    "group": [
        "groups", "chama", "vsla", "sacco", "circle", "club", "team",
        "association", "whole group", "everyone", "all of us", "the group",
    ],
    "total": [
        "totals", "sum", "sums", "overall", "altogether", "combined",
        "grand", "aggregate", "how much in total", "all",
    ],
    "status": [
        "state", "situation", "health", "condition", "standing",
        "overview", "summary", "report", "picture",
    ],
    "inactive": [
        "idle", "silent", "missing", "absent", "not deposited",
        "no deposit", "dormant", "quiet", "has not saved",
    ],
    "risk": [
        "risky", "danger", "exposure", "overexposed", "default",
        "defaulting", "bad loan", "too much loan",
    ],
    "trend": [
        "trends", "growing", "growth", "falling", "dropped", "drop",
        "increase", "increased", "decrease", "this week", "last week",
        "this month", "movement",
    ],
    "top": [
        "best", "highest", "biggest", "leading", "most",
    ],
    "compare": [
        "versus", "vs", "against", "difference", "more than", "less than",
        "bigger", "smaller",
    ],
    "how_much": [
        "how much", "howmany", "how many", "what is", "whats", "what's",
        "tell me", "show", "give me", "amount",
    ],
    "who": [
        "which member", "which person", "whose",
    ],
}

# Phrases that should survive even if other words vanish.
PHRASE_ALIASES: dict[str, list[str]] = {
    "total_savings": [
        "total savings", "total saving", "all savings", "group savings",
        "how much saved", "how much have we saved", "savings so far",
        "money saved", "total deposit", "total deposits", "total deposite",
        "sum of savings", "savings total", "saved altogether",
        "how much is saved", "savings of the group",
    ],
    "total_loans": [
        "total loans", "total loan", "all loans", "group loans",
        "how much loaned", "how much lent", "outstanding loans",
        "loans out", "money out on loan", "sum of loans",
        "how much is owed", "how much do members owe",
    ],
    "group_balance": [
        "group balance", "available balance", "cash balance",
        "money left", "what is left", "cash at hand", "money on hand",
        "how much do we have", "how much is available",
        "treasury", "remaining cash",
    ],
    "total_welfare": [
        "total welfare", "welfare total", "welfare collected",
        "welfare fund", "social fund total",
    ],
    "total_fines": [
        "total fines", "fines total", "all fines", "penalties total",
        "how much in fines",
    ],
    "total_profit": [
        "total profit", "profits", "how much profit", "interest plus fines",
        "what did we earn", "earnings", "surplus",
    ],
    "member_count": [
        "how many members", "member count", "number of members",
        "people in the group", "size of the group",
    ],
    "inactive_members": [
        "who has not deposited", "who did not save", "inactive members",
        "members with no deposit", "who is quiet", "who is missing",
        "who has not paid in", "no deposit this week",
    ],
    "top_savers": [
        "top savers", "best savers", "who saved most", "highest savings",
        "biggest saver", "leading savers",
    ],
    "biggest_loan": [
        "who owes most", "biggest loan", "largest loan", "highest loan",
        "who borrowed most",
    ],
    "overexposed": [
        "loan bigger than savings", "borrowed more than saved",
        "over exposed", "overexposed", "loan exceeds savings",
        "who is at risk",
    ],
    "repeat_fines": [
        "repeat fines", "fined many times", "who gets fined",
        "repeat offenders", "most fined",
    ],
    "savings_trend": [
        "savings trend", "are we growing", "is saving increasing",
        "this week vs last week", "trend",
    ],
    "loan_exposure": [
        "loan exposure", "loans versus savings", "loan to savings",
        "are loans too high", "loan ratio",
    ],
    "member_savings": [
        "how much has saved", "savings of", "deposit of",
        "what has saved", "balance of",
    ],
    "member_loan": [
        "how much does owe", "loan of", "owes how much",
        "outstanding for", "borrowed by",
    ],
    "member_fines": [
        "fines of", "fine of", "penalties for",
    ],
    "member_welfare": [
        "welfare of", "welfare for",
    ],
    "member_payments": [
        "payments of", "has paid back", "repaid by",
    ],
    "interest_on_loan": [
        "interest on", "interest for", "how much interest",
    ],
    "net_position": [
        "net position", "net worth in group", "what is left after loan",
        "savings minus loan",
    ],
    "group_health": [
        "how is the group", "group health", "are we okay",
        "financial health", "overview", "summary", "report",
        "situation", "how are we doing",
        "give me a brief", "health snapshot", "are we safe",
        "how are the books", "how is chama", "how is the chama",
    ],
    "recommendations": [
        "what should we do", "what do we do next", "advice",
        "recommend", "recommendations", "next steps",
        "how do we fix this", "action plan",
    ],
    "risk_register": [
        "who is at risk", "risk list", "watchlist",
        "who should we worry about", "danger list",
        "risky members", "problem members",
    ],
    "concentration": [
        "who holds the money", "is money concentrated",
        "few members have all", "spread of savings",
    ],
    "meeting_help": [
        "meeting agenda", "how to run the meeting",
        "how should we meet", "sitting agenda",
    ],
    "shareout_help": [
        "share out", "shareout", "how to divide",
        "end of cycle", "how do we share profit",
    ],
    "loan_policy": [
        "should we lend", "approve loan", "give a loan",
        "is this loan safe", "loan request",
    ],
    "what_if_payment": [
        "if we pay", "if they pay", "after paying", "if payment of",
    ],
    "what_if_deposit": [
        "if we save", "if they deposit", "after depositing",
        "if we add",
    ],
    "share_each": [
        "share per member", "each member gets", "divide equally",
        "split the profit", "dividend per person",
    ],
}

# Tiny function-word repairs applied before lexicon matching.
FUNCTION_TYPOS = {
    "wat": "what", "wht": "what", "whats": "what", "waht": "what",
    "mch": "much", "much": "much", "mchh": "much",
    "mny": "many", "meny": "many",
    "grp": "group", "gorup": "group", "grpup": "group", "grop": "group",
    "nt": "not", "nott": "not", "dont": "not", "doesn't": "not", "doesnt": "not",
    "pls": "please", "plz": "please",
    "u": "you", "ur": "your",
    "hw": "how", "hwo": "how",
    "teh": "the", "th": "the",
    "is": "is", "iz": "is",
    "our": "our", "owr": "our",
    "who": "who", "whos": "who", "whom": "who",
    "if": "if",
    "vs": "versus",
}

FINANCE_WORDS = {
    "savings", "saving", "save", "saved", "deposit", "deposite", "loan",
    "loans", "interest", "welfare", "fine", "fines", "payment", "balance",
    "profit", "member", "members", "group", "total", "status", "inactive",
    "risk", "trend", "top", "compare", "how_much", "who", "share", "each",
}

STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is",
    "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "has", "have", "had", "will", "would", "can", "could", "should",
    "please", "kindly", "just", "me", "my", "our", "we", "us", "you",
    "your", "their", "they", "them", "this", "that", "these", "those",
    "with", "from", "at", "by", "as", "if", "then", "than", "about",
    "into", "over", "also", "very", "so", "now", "up", "out", "there",
    "here", "what", "which", "when", "where", "why", "how",
}

# Words we must NOT treat as stopwords when scoring intents.
KEEP_WORDS = {
    "who", "how", "much", "many", "total", "all", "not", "no", "left",
    "most", "best", "vs", "plus", "minus", "each", "if", "owe", "owes",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000, "million": 1_000_000,
}


# ---------------------------------------------------------------------------
# Intent definitions
# ---------------------------------------------------------------------------

@dataclass
class IntentSpec:
    name: str
    keywords: set[str]
    phrases: set[str]
    min_hits: int = 1
    needs_member: bool = False
    is_math: bool = False
    description: str = ""


INTENTS: list[IntentSpec] = [
    IntentSpec("total_savings", {"savings", "total", "deposit", "saved", "group"}, {"total savings", "how much saved", "all savings"}, description="Group total savings"),
    IntentSpec("total_loans", {"loan", "loans", "owed", "borrow", "outstanding"}, {"total loans", "how much owed", "all loans"}, description="Group outstanding loans"),
    IntentSpec("group_balance", {"balance", "available", "left", "cash", "treasury"}, {"group balance", "money left", "cash at hand"}, description="Savings minus loan principal"),
    IntentSpec("total_welfare", {"welfare", "social"}, {"total welfare", "welfare fund"}, description="Welfare collected"),
    IntentSpec("total_fines", {"fine", "fines", "penalty"}, {"total fines", "all fines"}, description="Fines collected"),
    IntentSpec("total_profit", {"profit", "surplus", "earn", "dividend", "earnings"}, {"total profit", "how much profit"}, description="Interest money + fines"),
    IntentSpec("member_count", {"member", "members", "people", "count", "many"}, {"how many members", "member count"}, description="Number of members"),
    IntentSpec("inactive_members", {"inactive", "not", "missing", "dormant", "quiet", "deposit"}, {"who has not deposited", "inactive members"}, description="No deposit in 7 days"),
    IntentSpec("top_savers", {"top", "best", "highest", "most", "saver", "savers"}, {"top savers", "who saved most"}, description="Highest savers"),
    IntentSpec("biggest_loan", {"biggest", "largest", "most", "owe", "owes", "loan"}, {"who owes most", "biggest loan"}, description="Largest outstanding loan"),
    IntentSpec("overexposed", {"risk", "over", "exceed", "bigger", "more", "loan", "savings"}, {"loan bigger than savings", "overexposed"}, description="Loan exceeds own savings"),
    IntentSpec("repeat_fines", {"repeat", "fined", "twice", "many", "fine"}, {"repeat fines", "most fined"}, description="Members fined 2+ times"),
    IntentSpec("savings_trend", {"trend", "week", "month", "growing", "falling", "increase", "decrease"}, {"savings trend", "this week"}, description="Recent savings direction"),
    IntentSpec("loan_exposure", {"exposure", "ratio", "versus", "against", "loan", "savings"}, {"loan to savings", "loan exposure"}, description="Loans vs savings ratio"),
    IntentSpec("group_health", {"health", "overview", "summary", "report", "doing", "situation", "status"}, {"how is the group", "group health"}, description="Overall health"),
    IntentSpec("member_savings", {"savings", "saved", "deposit", "deposite", "balance"}, {"how much has saved"}, needs_member=True, description="One member's savings"),
    IntentSpec("member_loan", {"loan", "owe", "owes", "owed", "borrow", "debt"}, {"how much does owe"}, needs_member=True, description="One member's loan"),
    IntentSpec("member_fines", {"fine", "fines", "penalty"}, {"fines of"}, needs_member=True, description="One member's fines"),
    IntentSpec("member_welfare", {"welfare"}, {"welfare of"}, needs_member=True, description="One member's welfare"),
    IntentSpec("member_payments", {"payment", "paid", "repay", "repaid", "cleared"}, {"has paid back"}, needs_member=True, description="One member's repayments"),
    IntentSpec("member_interest", {"interest", "rate", "percent"}, {"interest on"}, needs_member=True, description="Interest on a member's loan"),
    IntentSpec("member_net", {"net", "left", "after", "minus"}, {"savings minus loan"}, needs_member=True, description="Member savings minus loan"),
    IntentSpec("member_status", {"status", "account", "profile", "details"}, set(), needs_member=True, description="Full member snapshot"),
    IntentSpec("share_each", {"share", "each", "split", "divide", "per", "dividend"}, {"share per member", "divide equally"}, is_math=True, description="Equal split of a pot"),
    IntentSpec("interest_calc", {"interest", "percent", "rate", "calculate", "calc"}, {"interest on"}, is_math=True, description="Interest calculation"),
    IntentSpec("what_if_payment", {"if", "pay", "payment", "after"}, {"if we pay", "after paying"}, is_math=True, description="Remaining loan after a payment"),
    IntentSpec("what_if_deposit", {"if", "save", "deposit", "add"}, {"if we save", "if they deposit"}, is_math=True, description="New totals after a deposit"),
    IntentSpec("compare_members", {"compare", "versus", "vs", "difference", "more", "less"}, set(), description="Compare two members"),
    IntentSpec("list_members", {"list", "show", "all", "members", "names"}, {"list members"}, description="List members"),
    IntentSpec("recent_activity", {"recent", "latest", "last", "activity", "records", "history"}, {"recent activity"}, description="Latest records"),
    IntentSpec("recommendations", {"recommend", "advice", "should", "next", "plan", "what do we do"}, {"what should we do", "recommendations"}, description="Next actions from patterns"),
    IntentSpec("meeting_help", {"meeting", "agenda", "sitting", "how we meet"}, {"meeting agenda", "how should we meet"}, description="How to run the sitting"),
    IntentSpec("shareout_help", {"shareout", "share out", "divide profit", "end of cycle"}, {"share out", "how to share out"}, description="Share-out checklist"),
    IntentSpec("loan_policy", {"approve", "should we lend", "give loan", "loan request"}, {"should we give a loan", "approve this loan"}, description="Loan approval questions"),
    IntentSpec("concentration", {"concentrat", "few members", "too much with one", "spread"}, {"who holds most of the money"}, description="Savings and loan concentration"),
    IntentSpec("risk_register", {"risk register", "who is risky", "danger list", "watchlist"}, {"who is at risk", "risk list"}, description="Ranked member risk"),
    IntentSpec("recommendations_member", {"advice for", "what should", "talk to"}, {"what should we tell"}, needs_member=True, description="Talking points for one member"),
]


# ---------------------------------------------------------------------------
# Build the lookup indexes once
# ---------------------------------------------------------------------------

def _collect_base_terms() -> dict[str, str]:
    """Map alias -> canonical word."""
    mapping: dict[str, str] = {}
    for canon, aliases in CANONICAL_WORDS.items():
        mapping[fold(canon)] = canon
        for alias in aliases:
            mapping[fold(alias)] = canon
    extra = {
        "ugx": "money", "shilling": "money", "shillings": "money",
        "bob": "money", "cash": "balance", "owe": "loan", "owes": "loan",
        "owed": "loan", "borrowed": "loan", "lent": "loan",
        "saverd": "savings", "savng": "savings", "savngs": "savings",
        "depo": "savings", "dep": "savings", "contribtion": "savings",
        "intrest": "interest", "intreast": "interest", "interst": "interest",
        "welfer": "welfare", "welfar": "welfare",
        "penality": "fine", "penelty": "fine",
        "repayed": "payment", "payed": "payment",
        "ballance": "balance", "balence": "balance",
        "profitt": "profit", "proft": "profit",
        "membr": "member", "membar": "member",
        "grpup": "group", "gorup": "group",
        "totl": "total", "tota": "total",
        "inactiv": "inactive", "inacive": "inactive",
    }
    mapping.update(extra)
    return mapping


BASE_TERMS = _collect_base_terms()


def _build_variant_index(base_terms: dict[str, str]) -> dict[str, str]:
    """
    Expand every lexicon word with missing-letter, swap, neighbor and
    double-letter variants. This is the 'understand even if a letter is
    missing' engine.
    """
    index: dict[str, str] = dict(base_terms)
    # Also index multi-word aliases as joined tokens.
    for alias, canon in list(base_terms.items()):
        parts = alias.split()
        if len(parts) == 1 and len(alias) >= 3:
            for variant in missing_letter_variants(alias):
                index.setdefault(variant, canon)
            for variant in swapped_variants(alias):
                index.setdefault(variant, canon)
            # Neighbor variants explode quickly; keep only length >= 4.
            if len(alias) >= 4:
                for variant in neighbor_variants(alias):
                    index.setdefault(variant, canon)
            for variant in extra_letter_variants(alias):
                if len(variant) <= 14:
                    index.setdefault(variant, canon)
            skeleton = vowel_drop_variant(alias)
            if len(skeleton) >= 3:
                index.setdefault(skeleton, canon)
            index.setdefault(soundex(alias).lower(), canon)
            index.setdefault("ph:" + metaphone_lite(alias), canon)
    return index


VARIANT_INDEX = _build_variant_index(BASE_TERMS)


def correct_token(token: str) -> tuple[str, float]:
    """Map one messy token onto a canonical word plus confidence."""
    raw = fold(token)
    if not raw:
        return raw, 0.0
    if raw in FUNCTION_TYPOS:
        return FUNCTION_TYPOS[raw], 0.99
    if raw in STOPWORDS and raw not in KEEP_WORDS:
        return raw, 0.0
    if raw.isdigit():
        return raw, 1.0
    if raw in BASE_TERMS:
        return BASE_TERMS[raw], 1.0
    # Variant index is noisy for 1-3 letter fragments.
    if len(raw) >= 4 and raw in VARIANT_INDEX:
        return VARIANT_INDEX[raw], 0.9
    if len(raw) >= 5:
        skeleton = vowel_drop_variant(raw)
        if skeleton in VARIANT_INDEX:
            return VARIANT_INDEX[skeleton], 0.8

    best_word = raw
    best_score = 0.0
    for alias, canon in BASE_TERMS.items():
        if " " in alias:
            continue
        if abs(len(alias) - len(raw)) > 2:
            continue
        score = similarity(raw, alias)
        if score > best_score:
            best_score = score
            best_word = canon
    threshold = 0.75 if len(raw) <= 4 else 0.78
    if best_score >= threshold:
        return best_word, best_score
    return raw, 0.0


def normalize_question(text: str) -> dict[str, Any]:
    raw_tokens = tokens(text)
    corrected = []
    confidences = []
    for tok in raw_tokens:
        word, conf = correct_token(tok)
        corrected.append(word)
        confidences.append(conf)
    return {
        "original": text,
        "folded": fold(text),
        "raw_tokens": raw_tokens,
        "corrected_tokens": corrected,
        "correction_confidence": (
            sum(confidences) / len(confidences) if confidences else 0.0
        ),
        "corrected_text": " ".join(corrected),
    }


# ---------------------------------------------------------------------------
# Amount / percent / member extraction
# ---------------------------------------------------------------------------

AMOUNT_RE = re.compile(
    r"(?:ugx|ush|shs|shillings?|ksh)?\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.[0-9]+)?)\s*(?:ugx|ush|shs|k|ksh|grand|million|m)?"
    ,
    re.I,
)
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*percent", re.I)


def parse_number_token(tok: str) -> float | None:
    tok = tok.lower().replace(",", "")
    if tok in NUMBER_WORDS:
        return float(NUMBER_WORDS[tok])
    if re.fullmatch(r"\d+(\.\d+)?", tok):
        return float(tok)
    if tok.endswith("k") and re.fullmatch(r"\d+(\.\d+)?k", tok):
        return float(tok[:-1]) * 1000
    if tok.endswith("m") and re.fullmatch(r"\d+(\.\d+)?m", tok):
        return float(tok[:-1]) * 1_000_000
    return None


def extract_amounts(text: str) -> list[int]:
    found: list[int] = []
    folded = fold(text)
    for match in AMOUNT_RE.finditer(text + " " + folded):
        raw = match.group(1).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        tail = match.group(0).lower()
        if tail.endswith("k") or " k" in tail or tail.endswith("grand"):
            value *= 1000
        if "million" in tail or tail.endswith("m"):
            value *= 1_000_000
        found.append(int(round(value)))
    # Also walk tokens for bare numbers and number-words.
    toks = tokens(text)
    i = 0
    while i < len(toks):
        n = parse_number_token(toks[i])
        if n is not None:
            if i + 1 < len(toks) and toks[i + 1] in {"thousand", "k"}:
                n *= 1000
                i += 1
            elif i + 1 < len(toks) and toks[i + 1] in {"million", "m"}:
                n *= 1_000_000
                i += 1
            found.append(int(round(n)))
        i += 1
    # Unique, keep order.
    out: list[int] = []
    seen = set()
    for n in found:
        if n not in seen and n >= 0:
            seen.add(n)
            out.append(n)
    return out


def extract_percents(text: str) -> list[float]:
    out: list[float] = []
    for match in PERCENT_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        try:
            out.append(float(raw))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Exact money math — never leave this to the language model
# ---------------------------------------------------------------------------

def ugx(value: Any) -> int:
    """Whole Uganda shillings. Half-up rounding, never float leftovers."""
    if value is None:
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number):
        return 0
    return int(math.floor(number + 0.5))


def pct(part: Any, whole: Any) -> float:
    whole_n = float(whole or 0)
    if whole_n == 0:
        return 0.0
    return round(float(part or 0) * 100.0 / whole_n, 2)


def ratio(num: Any, den: Any) -> float:
    den_n = float(den or 0)
    if den_n == 0:
        return 0.0
    return round(float(num or 0) / den_n, 4)


def interest_on(principal: Any, rate_percent: Any) -> int:
    """Interest money = principal × rate ÷ 100. Rate is a percent, not a fraction."""
    p = float(principal or 0)
    r = float(rate_percent or 0)
    if p <= 0 or r <= 0:
        return 0
    return ugx(p * r / 100.0)


def amount_with_interest(principal: Any, rate_percent: Any) -> int:
    p = ugx(principal)
    return p + interest_on(p, rate_percent)


def apply_payment(remaining: Any, payment: Any) -> dict[str, int]:
    rem = ugx(remaining)
    pay = ugx(payment)
    if pay < 0:
        pay = 0
    applied = pay if pay < rem else rem
    leftover_payment = pay - applied
    new_remaining = rem - applied
    return {
        "applied": applied,
        "remaining": new_remaining,
        "unused_payment": leftover_payment,
        "cleared": 1 if new_remaining == 0 else 0,
    }


def add_money(*values: Any) -> int:
    return ugx(sum(ugx(v) for v in values))


def sub_money(left: Any, right: Any) -> int:
    return ugx(left) - ugx(right)


def safe_div(num: Any, den: Any) -> float:
    d = float(den or 0)
    if d == 0:
        return 0.0
    return float(num or 0) / d


def equal_share(total: Any, heads: Any) -> dict[str, Any]:
    pot = ugx(total)
    people = int(heads or 0)
    if people <= 0:
        return {"share": 0, "remainder": pot, "people": 0, "total": pot}
    share = pot // people
    remainder = pot - share * people
    return {"share": share, "remainder": remainder, "people": people, "total": pot}


def change_and_pct(new: Any, old: Any) -> dict[str, Any]:
    n, o = ugx(new), ugx(old)
    delta = n - o
    return {
        "new": n,
        "old": o,
        "change": delta,
        "percent": pct(delta, o) if o else (100.0 if n else 0.0),
        "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
    }


# ---------------------------------------------------------------------------
# Database snapshot used by both understanding and math
# Works with SQLite files and PostgreSQL/Supabase via DATABASE_URL.
# ---------------------------------------------------------------------------

SQLITE_FALLBACK_PATH = "SAVIO-database/SAVIO.db"


def _env_database_url() -> str | None:
    return os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL")


def _looks_like_dsn(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.strip().lower()
    return lowered.startswith(("postgresql://", "postgres://", "postgresql+psycopg2://"))


def _resolve_target(db_path: str | None = None) -> str:
    """Explicit path/URL wins. Otherwise DATABASE_URL, else local SQLite."""
    if db_path:
        return db_path
    return _env_database_url() or SQLITE_FALLBACK_PATH


def _is_postgres(db_path: str | None) -> bool:
    """True for a Postgres URL argument, or when env points at Postgres and no file path was given."""
    if _looks_like_dsn(db_path):
        return True
    if db_path:
        return False
    return _looks_like_dsn(_env_database_url())


def _postgres_url(url: str) -> str:
    """Normalize a Supabase / Postgres URL so psycopg2 can connect over SSL."""
    if not url:
        return url
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url[len("postgresql+psycopg2://"):]
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "sslmode" not in query:
        query["sslmode"] = ["require"]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _sql(query: str, postgres: bool) -> str:
    """SQLite uses ? placeholders; PostgreSQL uses %s. Do not rewrite other SQL."""
    if postgres:
        return query.replace("?", "%s")
    return query


def _execute(cursor, postgres: bool, query: str, params=None):
    """Run a query with the placeholder style of the active engine."""
    adapted = _sql(query, postgres)
    if params is None:
        return cursor.execute(adapted)
    return cursor.execute(adapted, params)


def _first_value(row: Any) -> Any:
    """Works for tuples, sqlite3.Row and psycopg2 DictRow."""
    if row is None:
        return None
    try:
        return row[0]
    except (KeyError, IndexError, TypeError):
        if isinstance(row, dict):
            return next(iter(row.values()))
        return row


def _row_id(row: Any) -> Any:
    """Member id from a DictCursor / sqlite3.Row / tuple row."""
    try:
        return row["id"]
    except (KeyError, TypeError, IndexError):
        return _first_value(row)


def _conn(db_path: str | None = None):
    target = _resolve_target(db_path)
    if _is_postgres(target):
        url = target if _looks_like_dsn(target) else _env_database_url()
        if not url:
            raise RuntimeError("PostgreSQL database URL is not configured.")
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError("psycopg2 is required for PostgreSQL. Install psycopg2-binary.")
        return psycopg2.connect(_postgres_url(url), cursor_factory=DictCursor)

    sqlite_path = target or SQLITE_FALLBACK_PATH
    parent = os.path.dirname(sqlite_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(sqlite_path)
    connection.row_factory = sqlite3.Row
    return connection


def load_snapshot(db_path: str | None = None) -> dict[str, Any]:
    """
    Read members, loans, welfare, fines, payments and deposits.

    db_path may be:
      * a PostgreSQL/Supabase URL
      * a SQLite filesystem path
      * None → DATABASE_URL / SUPABASE_DB_URL, else local SQLite
    """
    target = _resolve_target(db_path)
    postgres = _is_postgres(target)
    connection = _conn(target)
    try:
        cur = connection.cursor()

        _execute(
            cur,
            postgres,
            "SELECT id, firstname, surname, number, surety, deposite, status FROM members",
        )
        members = []
        for row in cur.fetchall():
            members.append({
                "id": row["id"],
                "firstname": row["firstname"] or "",
                "surname": row["surname"] or "",
                "name": f"{(row['firstname'] or '').strip()} {(row['surname'] or '').strip()}".strip(),
                "number": row["number"] or "",
                "surety": row["surety"] or "",
                "savings": ugx(row["deposite"]),
                "status": row["status"] or "",
            })

        _execute(cur, postgres, "SELECT member_id, amount, interest FROM loan")
        loans = {
            row["member_id"]: {
                "principal": ugx(row["amount"]),
                "rate": float(row["interest"] or 0),
            }
            for row in cur.fetchall()
        }

        _execute(
            cur,
            postgres,
            "SELECT member_id, COALESCE(SUM(amount),0) AS total FROM welfare GROUP BY member_id",
        )
        welfare = {row["member_id"]: ugx(row["total"]) for row in cur.fetchall()}

        _execute(
            cur,
            postgres,
            "SELECT member_id, COALESCE(SUM(amount),0) AS total FROM fine GROUP BY member_id",
        )
        fines = {row["member_id"]: ugx(row["total"]) for row in cur.fetchall()}

        _execute(
            cur,
            postgres,
            "SELECT member_id, COALESCE(SUM(payment),0) AS total FROM payment GROUP BY member_id",
        )
        payments = {row["member_id"]: ugx(row["total"]) for row in cur.fetchall()}

        _execute(cur, postgres, "SELECT COALESCE(SUM(amount),0) FROM deposit")
        total_savings = ugx(_first_value(cur.fetchone()))

        _execute(cur, postgres, "SELECT COALESCE(SUM(amount),0) FROM welfare")
        total_welfare = ugx(_first_value(cur.fetchone()))

        _execute(cur, postgres, "SELECT COALESCE(SUM(amount),0) FROM fine")
        total_fines = ugx(_first_value(cur.fetchone()))

        _execute(cur, postgres, "SELECT COALESCE(SUM(payment),0) FROM payment")
        total_payments = ugx(_first_value(cur.fetchone()))

        now = datetime.now()
        week_start = now - timedelta(days=7)
        last_week_start = now - timedelta(days=14)

        _execute(
            cur,
            postgres,
            "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ?",
            (week_start,),
        )
        this_week = ugx(_first_value(cur.fetchone()))
        _execute(
            cur,
            postgres,
            "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ? AND created_at < ?",
            (last_week_start, week_start),
        )
        last_week = ugx(_first_value(cur.fetchone()))

        weekly = []
        for i in range(3, -1, -1):
            start = now - timedelta(days=7 * (i + 1))
            end = now - timedelta(days=7 * i)
            _execute(
                cur,
                postgres,
                "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ? AND created_at < ?",
                (start, end),
            )
            weekly.append(ugx(_first_value(cur.fetchone())))

        _execute(
            cur,
            postgres,
            """
            SELECT id FROM members
            WHERE id NOT IN (SELECT member_id FROM deposit WHERE created_at >= ?)
            """,
            (week_start,),
        )
        inactive_ids = {_row_id(row) for row in cur.fetchall()}
    finally:
        connection.close()

    total_loan_principal = 0
    total_interest_money = 0
    total_loans_with_interest = 0
    enriched = []
    for member in members:
        loan = loans.get(member["id"], {"principal": 0, "rate": 0.0})
        interest_money = interest_on(loan["principal"], loan["rate"])
        due = loan["principal"] + interest_money
        total_loan_principal += loan["principal"]
        total_interest_money += interest_money
        total_loans_with_interest += due
        item = dict(member)
        item.update({
            "loan_principal": loan["principal"],
            "loan_rate": loan["rate"],
            "loan_interest_money": interest_money,
            "loan_total_due": due,
            "welfare": welfare.get(member["id"], 0),
            "fines": fines.get(member["id"], 0),
            "payments": payments.get(member["id"], 0),
            "net": member["savings"] - loan["principal"],
            "overexposed": loan["principal"] > member["savings"] and loan["principal"] > 0,
        })
        enriched.append(item)

    profit = total_interest_money + total_fines
    balance_vs_principal = total_savings - total_loan_principal
    balance_vs_due = total_savings - total_loans_with_interest
    exposure = ratio(total_loans_with_interest, total_savings)

    if total_loans_with_interest == 0:
        exposure_label = "no outstanding loans"
    elif exposure < 0.3:
        exposure_label = "conservative"
    elif exposure < 0.7:
        exposure_label = "moderate"
    else:
        exposure_label = "high"

    for item in enriched:
        item["inactive_7d"] = item["id"] in inactive_ids

    return {
        "members": enriched,
        "totals": {
            "savings": total_savings,
            "loan_principal": total_loan_principal,
            "loan_interest_money": total_interest_money,
            "loans_with_interest": total_loans_with_interest,
            "balance_vs_principal": balance_vs_principal,
            "balance_vs_due": balance_vs_due,
            "welfare": total_welfare,
            "fines": total_fines,
            "payments": total_payments,
            "profit": profit,
            "member_count": len(enriched),
            "inactive_count": len(inactive_ids),
            "overexposed_count": sum(1 for m in enriched if m["overexposed"]),
            "this_week_savings": this_week,
            "last_week_savings": last_week,
            "weekly_trend": weekly,
            "loan_to_savings_ratio": exposure,
            "exposure_label": exposure_label,
        },
        "trend": change_and_pct(this_week, last_week),
    }


def format_ugx(value: Any) -> str:
    return f"UGX {ugx(value):,}"


def verify_database(db_path: str | None = None) -> dict[str, Any]:
    """
    Compact compatibility check: connect, read members/savings/loans,
    build a snapshot, and confirm interest math stays in Python.
    Does not call Gemini and does not print secrets.
    """
    target = _resolve_target(db_path)
    engine = "postgresql" if _is_postgres(target) else "sqlite"
    connection = _conn(target)
    try:
        cur = connection.cursor()
        _execute(cur, engine == "postgresql", "SELECT id, firstname, surname, deposite FROM members")
        members = cur.fetchall()
        _execute(cur, engine == "postgresql", "SELECT COALESCE(SUM(amount), 0) FROM deposit")
        savings_sum = ugx(_first_value(cur.fetchone()))
        _execute(cur, engine == "postgresql", "SELECT member_id, amount, interest FROM loan")
        loans = cur.fetchall()
    finally:
        connection.close()

    snapshot = load_snapshot(target)
    sample_interest = interest_on(10000, 10)
    sample_total = amount_with_interest(10000, 10)
    return {
        "ok": True,
        "engine": engine,
        "member_count": len(members),
        "savings_readable": True,
        "savings_total": savings_sum,
        "loan_rows": len(loans),
        "snapshot_members": len(snapshot.get("members") or []),
        "snapshot_savings": snapshot["totals"]["savings"],
        "interest_math": {
            "formula": "interest = principal * rate / 100; total = principal + interest",
            "example_principal": 10000,
            "example_rate": 10,
            "example_interest": sample_interest,
            "example_total": sample_total,
            "correct": sample_interest == 1000 and sample_total == 11000,
        },
    }


# ---------------------------------------------------------------------------
# Member resolution (missing letters in names too)
# ---------------------------------------------------------------------------

def _name_keys(member: dict[str, Any]) -> list[str]:
    first = fold(member.get("firstname", ""))
    last = fold(member.get("surname", ""))
    full = fold(member.get("name", ""))
    keys = [first, last, full, f"{first} {last}", f"{last} {first}"]
    if member.get("id") is not None:
        keys.append(str(member["id"]))
        keys.append(f"#{member['id']}")
    return [k for k in keys if k]


def score_member(query_tokens: list[str], member: dict[str, Any]) -> float:
    if not query_tokens:
        return 0.0
    names = _name_keys(member)
    score = 0.0
    for tok in query_tokens:
        raw = fold(tok)
        if raw in STOPWORDS:
            continue
        if raw in FINANCE_WORDS or raw in BASE_TERMS:
            continue
        if raw.isdigit() and int(raw) == member["id"]:
            score += 2.5
            continue
        if len(raw) < 3:
            continue
        best = 0.0
        for name in names:
            parts = [p for p in name.split() if p]
            for part in parts:
                best = max(best, similarity(raw, part))
                if len(raw) >= 4 and soundex(raw) == soundex(part):
                    best = max(best, 0.8)
                if len(raw) >= 4 and metaphone_lite(raw) == metaphone_lite(part):
                    best = max(best, 0.78)
                if raw in missing_letter_variants(part) or part in missing_letter_variants(raw):
                    best = max(best, 0.9)
        if best >= 0.78:
            score += best
    return score


def resolve_members(question: str, snapshot: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    toks = tokens(question)
    pool = snapshot.get("members") or []
    ranked = []
    for member in pool:
        s = score_member(toks, member)
        if s >= 0.78:
            ranked.append((s, member))
    ranked.sort(key=lambda x: x[0], reverse=True)
    if not ranked:
        return []
    best = ranked[0][0]
    strong = [(s, m) for s, m in ranked if s >= max(0.9, best - 0.35)]
    return [{"score": round(s, 3), **m} for s, m in strong[:limit]]


# ---------------------------------------------------------------------------
# Intent scoring — works when words are missing
# ---------------------------------------------------------------------------

def _phrase_fuzzy_hit(phrase: str, text: str) -> float:
    """
    A phrase hits if most of its content words appear in the question,
    even if one word is missing and the rest are misspelt.
    """
    p_toks = [t for t in tokens(phrase) if t not in STOPWORDS or t in KEEP_WORDS]
    q_toks = tokens(text)
    if not p_toks:
        return 0.0
    hits = 0.0
    used = set()
    for p in p_toks:
        best_i = -1
        best = 0.0
        for i, q in enumerate(q_toks):
            if i in used:
                continue
            s = similarity(p, q)
            if s > best:
                best = s
                best_i = i
            # canonical correction of the question token
            cq, _ = correct_token(q)
            s2 = similarity(p, cq)
            if s2 > best:
                best = s2
                best_i = i
        if best >= 0.72:
            hits += best
            if best_i >= 0:
                used.add(best_i)
    return hits / len(p_toks)


def score_intent(spec: IntentSpec, original: str, corrected_text: str) -> float:
    phrase_score = 0.0
    phrases = list(spec.phrases) + list(PHRASE_ALIASES.get(spec.name, []))
    for phrase in phrases:
        phrase_score = max(phrase_score, _phrase_fuzzy_hit(phrase, original))
        phrase_score = max(phrase_score, _phrase_fuzzy_hit(phrase, corrected_text))

    q_toks = tokens(corrected_text) + tokens(original)
    unique_q = []
    seen_q = set()
    for q in q_toks:
        if q not in seen_q:
            seen_q.add(q)
            unique_q.append(q)

    strong_hits = 0
    hits = 0.0
    for kw in spec.keywords:
        best = 0.0
        for q in unique_q:
            best = max(best, similarity(kw, q))
            cq, _ = correct_token(q)
            if cq == kw:
                best = max(best, 1.0)
        if best >= 0.84:
            hits += best
            strong_hits += 1
    keyword_score = hits / max(1, len(spec.keywords))

    # Phrase match is the main signal. Loose keyword overlap is only a hint.
    score = phrase_score
    if phrase_score < 0.55 and strong_hits >= max(2, spec.min_hits + 1):
        score = max(score, min(0.82, 0.35 + 0.2 * strong_hits))
    elif phrase_score < 0.4 and keyword_score >= 0.5 and strong_hits >= 2:
        score = max(score, keyword_score * 0.7)
    return min(1.0, score)


def rank_intents(original: str, corrected_text: str, top_n: int = 4) -> list[dict[str, Any]]:
    ranked = []
    for spec in INTENTS:
        s = score_intent(spec, original, corrected_text)
        if s >= 0.42:
            ranked.append({
                "intent": spec.name,
                "score": round(s, 3),
                "needs_member": spec.needs_member,
                "is_math": spec.is_math,
                "description": spec.description,
            })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


# ---------------------------------------------------------------------------
# Answer computation from intent + snapshot
# ---------------------------------------------------------------------------

def _member_line(m: dict[str, Any]) -> str:
    return (
        f"#{m['id']} {m['name']}: savings {format_ugx(m['savings'])}, "
        f"loan {format_ugx(m['loan_principal'])} at {m['loan_rate']:g}%, "
        f"interest {format_ugx(m['loan_interest_money'])}, "
        f"due {format_ugx(m['loan_total_due'])}, "
        f"fines {format_ugx(m['fines'])}, welfare {format_ugx(m['welfare'])}, "
        f"repaid {format_ugx(m['payments'])}, net {format_ugx(m['net'])}"
    )


def compute_for_intent(
    intent: str,
    snapshot: dict[str, Any],
    members: list[dict[str, Any]],
    amounts: list[int],
    percents: list[float],
) -> dict[str, Any]:
    t = snapshot["totals"]
    facts: dict[str, Any] = {}
    spoken = ""

    if intent == "total_savings":
        facts = {"total_savings": t["savings"]}
        spoken = f"The group has saved {format_ugx(t['savings'])} in total."
    elif intent == "total_loans":
        facts = {
            "loan_principal": t["loan_principal"],
            "interest_money": t["loan_interest_money"],
            "loans_with_interest": t["loans_with_interest"],
        }
        spoken = (
            f"Outstanding loan principal is {format_ugx(t['loan_principal'])}. "
            f"Interest on that is {format_ugx(t['loan_interest_money'])}. "
            f"Total owed including interest is {format_ugx(t['loans_with_interest'])}."
        )
    elif intent == "group_balance":
        facts = {
            "savings": t["savings"],
            "loan_principal": t["loan_principal"],
            "balance_vs_principal": t["balance_vs_principal"],
            "balance_vs_due": t["balance_vs_due"],
        }
        spoken = (
            f"Cash after subtracting loan principal: {format_ugx(t['balance_vs_principal'])}. "
            f"If you also reserve interest due, {format_ugx(t['balance_vs_due'])} remains."
        )
    elif intent == "total_welfare":
        facts = {"total_welfare": t["welfare"]}
        spoken = f"Welfare collected is {format_ugx(t['welfare'])}."
    elif intent == "total_fines":
        facts = {"total_fines": t["fines"]}
        spoken = f"Fines collected are {format_ugx(t['fines'])}."
    elif intent == "total_profit":
        facts = {
            "interest_money": t["loan_interest_money"],
            "fines": t["fines"],
            "profit": t["profit"],
        }
        spoken = (
            f"Profit is interest money {format_ugx(t['loan_interest_money'])} "
            f"plus fines {format_ugx(t['fines'])} = {format_ugx(t['profit'])}."
        )
    elif intent == "member_count":
        facts = {"member_count": t["member_count"]}
        spoken = f"The group has {t['member_count']} member(s)."
    elif intent == "inactive_members":
        inactive = [m for m in snapshot["members"] if m.get("inactive_7d")]
        facts = {"inactive_count": len(inactive), "names": [m["name"] for m in inactive]}
        if inactive:
            spoken = "No deposit in the last 7 days: " + ", ".join(m["name"] for m in inactive) + "."
        else:
            spoken = "Every member has deposited in the last 7 days."
    elif intent == "top_savers":
        top = sorted(snapshot["members"], key=lambda m: m["savings"], reverse=True)[:3]
        facts = {"top": [{"name": m["name"], "savings": m["savings"]} for m in top if m["savings"] > 0]}
        if facts["top"]:
            spoken = "Top savers: " + "; ".join(f"{x['name']} {format_ugx(x['savings'])}" for x in facts["top"]) + "."
        else:
            spoken = "No savings recorded yet."
    elif intent == "biggest_loan":
        top = sorted(snapshot["members"], key=lambda m: m["loan_principal"], reverse=True)[:3]
        top = [m for m in top if m["loan_principal"] > 0]
        facts = {"top": [{"name": m["name"], "loan": m["loan_principal"], "due": m["loan_total_due"]} for m in top]}
        if top:
            spoken = "Biggest loans: " + "; ".join(
                f"{m['name']} {format_ugx(m['loan_principal'])} (due {format_ugx(m['loan_total_due'])})"
                for m in top
            ) + "."
        else:
            spoken = "There are no active loans."
    elif intent == "overexposed":
        risky = [m for m in snapshot["members"] if m["overexposed"]]
        facts = {"count": len(risky), "names": [m["name"] for m in risky]}
        if risky:
            spoken = "Loan bigger than own savings: " + "; ".join(
                f"{m['name']} owes {format_ugx(m['loan_principal'])} against {format_ugx(m['savings'])} saved"
                for m in risky
            ) + "."
        else:
            spoken = "No member currently owes more than they have saved."
    elif intent == "repeat_fines":
        # Snapshot does not include fine counts; approximate by fines > 0 list.
        fined = [m for m in snapshot["members"] if m["fines"] > 0]
        fined.sort(key=lambda m: m["fines"], reverse=True)
        facts = {"fined": [{"name": m["name"], "fines": m["fines"]} for m in fined]}
        if fined:
            spoken = "Members with fines: " + "; ".join(
                f"{m['name']} {format_ugx(m['fines'])}" for m in fined
            ) + "."
        else:
            spoken = "No fines on record."
    elif intent == "savings_trend":
        facts = {"weekly": t["weekly_trend"], "this_week": t["this_week_savings"], "last_week": t["last_week_savings"], "trend": snapshot["trend"]}
        tr = snapshot["trend"]
        spoken = (
            f"This week {format_ugx(tr['new'])}, last week {format_ugx(tr['old'])}. "
            f"Change {format_ugx(tr['change'])} ({tr['percent']}%, {tr['direction']})."
        )
    elif intent == "loan_exposure":
        facts = {
            "ratio": t["loan_to_savings_ratio"],
            "label": t["exposure_label"],
            "loans_with_interest": t["loans_with_interest"],
            "savings": t["savings"],
        }
        spoken = (
            f"Loans including interest {format_ugx(t['loans_with_interest'])} "
            f"against savings {format_ugx(t['savings'])} "
            f"(ratio {t['loan_to_savings_ratio']:.2f}, {t['exposure_label']})."
        )
    elif intent == "group_health":
        facts = dict(t)
        spoken = (
            f"Health snapshot: savings {format_ugx(t['savings'])}, "
            f"loans due {format_ugx(t['loans_with_interest'])}, "
            f"balance after principal {format_ugx(t['balance_vs_principal'])}, "
            f"profit {format_ugx(t['profit'])}, "
            f"exposure {t['exposure_label']}, "
            f"{t['inactive_count']} inactive in 7 days, "
            f"{t['overexposed_count']} over-borrowed."
        )
    elif intent == "list_members":
        facts = {"names": [f"#{m['id']} {m['name']}" for m in snapshot["members"]]}
        spoken = "Members: " + ", ".join(facts["names"]) if facts["names"] else "No members yet."
    elif intent == "share_each":
        pot = amounts[0] if amounts else t["profit"]
        people = t["member_count"]
        split = equal_share(pot, people)
        facts = split
        spoken = (
            f"{format_ugx(split['total'])} split across {split['people']} members "
            f"is {format_ugx(split['share'])} each, remainder {format_ugx(split['remainder'])}."
        )
    elif intent == "interest_calc":
        principal = amounts[0] if amounts else (members[0]["loan_principal"] if members else 0)
        rate = percents[0] if percents else (members[0]["loan_rate"] if members else 0)
        money = interest_on(principal, rate)
        facts = {"principal": ugx(principal), "rate": rate, "interest": money, "total": ugx(principal) + money}
        spoken = (
            f"Interest on {format_ugx(principal)} at {rate:g}% is {format_ugx(money)}. "
            f"Total due {format_ugx(facts['total'])}."
        )
    elif intent == "what_if_payment":
        pay = amounts[0] if amounts else 0
        remaining = members[0]["loan_principal"] if members else t["loan_principal"]
        result = apply_payment(remaining, pay)
        facts = result
        spoken = (
            f"A payment of {format_ugx(pay)} against {format_ugx(remaining)} applies "
            f"{format_ugx(result['applied'])}, leaves {format_ugx(result['remaining'])}, "
            f"unused {format_ugx(result['unused_payment'])}."
        )
    elif intent == "what_if_deposit":
        add = amounts[0] if amounts else 0
        facts = {"current": t["savings"], "added": add, "new_total": t["savings"] + ugx(add)}
        spoken = f"If {format_ugx(add)} is deposited, group savings become {format_ugx(facts['new_total'])}."
    elif intent.startswith("member_") and members:
        m = members[0]
        if intent == "member_savings":
            facts = {"name": m["name"], "savings": m["savings"]}
            spoken = f"{m['name']} has saved {format_ugx(m['savings'])}."
        elif intent == "member_loan":
            facts = {
                "name": m["name"],
                "principal": m["loan_principal"],
                "rate": m["loan_rate"],
                "interest": m["loan_interest_money"],
                "due": m["loan_total_due"],
            }
            spoken = (
                f"{m['name']} owes {format_ugx(m['loan_principal'])} principal "
                f"at {m['loan_rate']:g}% (interest {format_ugx(m['loan_interest_money'])}, "
                f"total due {format_ugx(m['loan_total_due'])})."
            )
        elif intent == "member_fines":
            facts = {"name": m["name"], "fines": m["fines"]}
            spoken = f"{m['name']} has {format_ugx(m['fines'])} in fines."
        elif intent == "member_welfare":
            facts = {"name": m["name"], "welfare": m["welfare"]}
            spoken = f"{m['name']} has {format_ugx(m['welfare'])} in welfare."
        elif intent == "member_payments":
            facts = {"name": m["name"], "payments": m["payments"]}
            spoken = f"{m['name']} has repaid {format_ugx(m['payments'])}."
        elif intent == "member_interest":
            facts = {"name": m["name"], "interest": m["loan_interest_money"], "rate": m["loan_rate"]}
            spoken = f"Interest on {m['name']}'s loan is {format_ugx(m['loan_interest_money'])} at {m['loan_rate']:g}%."
        elif intent == "member_net":
            facts = {"name": m["name"], "savings": m["savings"], "loan": m["loan_principal"], "net": m["net"]}
            spoken = (
                f"{m['name']}'s net (savings minus loan principal) is {format_ugx(m['net'])} "
                f"({format_ugx(m['savings'])} - {format_ugx(m['loan_principal'])})."
            )
        else:
            facts = {"member": {k: m[k] for k in ("id", "name", "savings", "loan_principal", "loan_rate", "loan_interest_money", "loan_total_due", "fines", "welfare", "payments", "net", "status")}}
            spoken = _member_line(m)
    elif intent == "compare_members" and len(members) >= 2:
        a, b = members[0], members[1]
        facts = {
            "a": {"name": a["name"], "savings": a["savings"], "loan": a["loan_principal"]},
            "b": {"name": b["name"], "savings": b["savings"], "loan": b["loan_principal"]},
            "savings_diff": a["savings"] - b["savings"],
            "loan_diff": a["loan_principal"] - b["loan_principal"],
        }
        spoken = (
            f"{a['name']} has {format_ugx(a['savings'])} saved and {format_ugx(a['loan_principal'])} loan. "
            f"{b['name']} has {format_ugx(b['savings'])} saved and {format_ugx(b['loan_principal'])} loan. "
            f"Savings difference {format_ugx(facts['savings_diff'])}."
        )
    elif intent == "recommendations":
        facts = {"profit": t["profit"], "inactive": t["inactive_count"], "overexposed": t["overexposed_count"]}
        spoken = (
            f"Start with the books: savings {format_ugx(t['savings'])}, "
            f"loans due {format_ugx(t['loans_with_interest'])}, "
            f"{t['inactive_count']} quiet this week, "
            f"{t['overexposed_count']} over-borrowed. "
            "Ask SAVIO for the named next steps from the pattern engine."
        )
    elif intent == "risk_register":
        risky = [
            m for m in snapshot["members"]
            if m.get("overexposed") or m.get("inactive_7d") and m.get("loan_principal", 0) > 0
        ]
        risky.sort(key=lambda m: (m.get("overexposed"), m.get("loan_principal", 0)), reverse=True)
        facts = {"count": len(risky), "names": [m["name"] for m in risky]}
        if risky:
            spoken = "Watchlist: " + "; ".join(
                f"{m['name']} loan {format_ugx(m['loan_principal'])} savings {format_ugx(m['savings'])}"
                + (" over-borrowed" if m.get("overexposed") else "")
                + (" quiet" if m.get("inactive_7d") else "")
                for m in risky[:8]
            ) + "."
        else:
            spoken = "No member currently sits on the combined quiet-or-over-borrowed watchlist."
    elif intent == "concentration":
        ordered = sorted(snapshot["members"], key=lambda m: m["savings"], reverse=True)
        top3 = ordered[:3]
        total = t["savings"] or 1
        share = sum(m["savings"] for m in top3)
        facts = {"top3": [{"name": m["name"], "savings": m["savings"]} for m in top3], "share": share}
        spoken = (
            "Top three savers hold "
            + ", ".join(f"{m['name']} {format_ugx(m['savings'])}" for m in top3 if m["savings"] > 0)
            + f" — {format_ugx(share)} of {format_ugx(t['savings'])}."
        )
    elif intent == "meeting_help":
        facts = {"topic": "agenda"}
        spoken = (
            "Run a short sitting: roll call, read last cash, collect savings, "
            "welfare and fines, collect repayments, review quiet borrowers, "
            "hear new loans against the buffer, write surety names, close."
        )
    elif intent == "shareout_help":
        facts = {"topic": "shareout", "profit": t["profit"], "members": t["member_count"]}
        split = equal_share(t["profit"], t["member_count"])
        spoken = (
            f"Before share-out, match the register to the box. "
            f"Current profit {format_ugx(t['profit'])} across {t['member_count']} members "
            f"is {format_ugx(split['share'])} each, remainder {format_ugx(split['remainder'])}. "
            "Do not share unpaid interest as if it were cash."
        )
    elif intent == "loan_policy":
        if members:
            m = members[0]
            facts = {"name": m["name"], "savings": m["savings"], "loan": m["loan_principal"], "inactive": m.get("inactive_7d")}
            spoken = (
                f"On {m['name']}: savings {format_ugx(m['savings'])}, "
                f"current loan {format_ugx(m['loan_principal'])}, "
                f"{'quiet this week' if m.get('inactive_7d') else 'deposited this week'}. "
                "Ask: is the new amount larger than their own savings, "
                "who is surety, and does the group still have a cash buffer."
            )
        else:
            facts = {"buffer": t["balance_vs_principal"], "exposure": t["loan_to_savings_ratio"]}
            spoken = (
                f"Group buffer after principal is {format_ugx(t['balance_vs_principal'])}, "
                f"exposure {t['loan_to_savings_ratio']:.2f} ({t['exposure_label']}). "
                "Do not approve a loan that spends the last 30 percent of savings."
            )
    elif intent == "recommendations_member" and members:
        m = members[0]
        facts = {"name": m["name"], "net": m["net"], "overexposed": m.get("overexposed"), "inactive": m.get("inactive_7d")}
        bits = [f"{m['name']}: savings {format_ugx(m['savings'])}, loan {format_ugx(m['loan_principal'])}, net {format_ugx(m['net'])}."]
        if m.get("overexposed"):
            bits.append("The loan is already larger than their savings — do not top it up.")
        if m.get("inactive_7d"):
            bits.append("They missed this week's deposit — call before the next sitting.")
        if m.get("fines"):
            bits.append(f"Fines on the book: {format_ugx(m['fines'])}.")
        spoken = " ".join(bits)
    else:
        spoken = ""

    return {"facts": facts, "exact_answer": spoken}


# ---------------------------------------------------------------------------
# Public pipeline
# ---------------------------------------------------------------------------

@dataclass
class Understanding:
    original: str
    corrected_text: str
    correction_confidence: float
    intents: list[dict[str, Any]]
    members: list[dict[str, Any]]
    amounts: list[int]
    percents: list[float]
    exact_answer: str
    facts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "corrected_text": self.corrected_text,
            "correction_confidence": round(self.correction_confidence, 3),
            "intents": self.intents,
            "members": [
                {"id": m["id"], "name": m["name"], "score": m.get("score")}
                for m in self.members
            ],
            "amounts": self.amounts,
            "percents": self.percents,
            "facts": self.facts,
            "exact_answer": self.exact_answer,
            "notes": self.notes,
        }


def understand_question(
    question: str,
    db_path: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> Understanding:
    norm = normalize_question(question or "")
    snap = snapshot if snapshot is not None else load_snapshot(db_path)
    intents = rank_intents(norm["original"], norm["corrected_text"])
    found_members = resolve_members(question, snap)
    amounts = extract_amounts(question)
    percents = extract_percents(question)

    notes: list[str] = []
    if norm["correction_confidence"] and norm["corrected_text"] != fold(question):
        notes.append(f"Read as: {norm['corrected_text']}")

    named = [m for m in found_members if m.get("score", 0) >= 0.9]
    top = intents[0]["intent"] if intents else ("member_status" if named else "group_health")
    folded_q = fold(question)
    if named:
        # A clearly named person turns a group question into that member's version
        # only when the user is asking about that person, not the whole group.
        groupish = any(w in folded_q for w in ("group", "all", "total", "everyone", "chama", "we "))
        if not groupish:
            member_map = {
                "total_savings": "member_savings",
                "total_loans": "member_loan",
                "total_fines": "member_fines",
                "total_welfare": "member_welfare",
                "total_profit": "member_interest",
                "group_balance": "member_net",
            }
            if top in member_map:
                top = member_map[top]
            member_intents = [i for i in intents if i["needs_member"]]
            if member_intents and intents and intents[0]["score"] - member_intents[0]["score"] <= 0.08:
                top = member_intents[0]["intent"]
            if "owe" in folded_q or "loan" in folded_q or "borrow" in folded_q:
                top = "member_loan"
            if "fine" in folded_q:
                top = "member_fines"
            if "welfare" in folded_q:
                top = "member_welfare"
            if "interest" in folded_q or "intrest" in folded_q or "intrst" in folded_q:
                top = "member_interest"
            if "pay" in folded_q or "repay" in folded_q:
                if "if" in folded_q:
                    top = "what_if_payment"
                else:
                    top = "member_payments"
            if "save" in folded_q or "saving" in folded_q or "deposit" in folded_q:
                if "if" not in folded_q and top not in ("member_loan", "member_interest", "what_if_payment"):
                    top = "member_savings"
    if "if" in folded_q and ("pay" in folded_q or "repay" in folded_q):
        top = "what_if_payment"
    if any(p in folded_q for p in ("each member", "per member", "split", "divide", "share the profit")):
        top = "share_each"
    if any(p in folded_q for p in ("who has not", "who did not", "inactive", "no deposit", "not deposited", "nt deposited")):
        top = "inactive_members"
    if any(p in folded_q for p in ("top saver", "best saver", "saved most", "top sav", "best sav")):
        top = "top_savers"
    toks_q = set(tokens(folded_q) + tokens(norm["corrected_text"]))
    if "top" in toks_q and ("savings" in toks_q or "saver" in toks_q or "savers" in toks_q):
        top = "top_savers"
    if any(p in folded_q for p in ("loan vs", "loans vs", "loan to saving", "exposure")):
        top = "loan_exposure"
    if any(p in folded_q for p in ("what should we do", "what do we do", "next step", "recommend", "advice")):
        if named:
            top = "recommendations_member"
        else:
            top = "recommendations"
    if any(p in folded_q for p in ("who is at risk", "watchlist", "risk list", "who should we worry")):
        top = "risk_register"
    if any(p in folded_q for p in ("agenda", "how should we meet", "run the meeting", "sitting")):
        top = "meeting_help"
    if any(p in folded_q for p in ("share out", "shareout", "end of cycle")):
        top = "shareout_help"
    if any(p in folded_q for p in ("should we lend", "approve the loan", "give a loan", "loan request")):
        top = "loan_policy"
    if any(p in folded_q for p in ("how is the group", "group health", "are we okay", "health report", "how are we doing")):
        top = "group_health"
    if named and folded_q.startswith("how is ") and "group" not in folded_q:
        top = "member_status"
    if named and any(p in folded_q for p in ("how is ", "hows ", "how's ")):
        if "group" not in folded_q and "chama" not in folded_q:
            top = "member_status"

    computed = compute_for_intent(top, snap, found_members, amounts, percents)
    if not computed["exact_answer"]:
        for item in intents:
            computed = compute_for_intent(item["intent"], snap, found_members, amounts, percents)
            if computed["exact_answer"]:
                top = item["intent"]
                break

    facts = dict(computed["facts"] or {})
    facts["resolved_intent"] = top
    return Understanding(
        original=question,
        corrected_text=norm["corrected_text"],
        correction_confidence=norm["correction_confidence"],
        intents=intents,
        members=found_members,
        amounts=amounts,
        percents=percents,
        exact_answer=computed["exact_answer"],
        facts=facts,
        notes=notes,
    )


def format_understanding_for_prompt(u: Understanding) -> str:
    """Block injected into the Gemini system prompt so math cannot drift."""
    lines = [
        "UNDERSTANDING LAYER (authoritative — use these numbers, do not recompute):",
        f"- User said: {u.original}",
        f"- Normalized as: {u.corrected_text or '(empty)'}",
    ]
    if u.intents:
        lines.append("- Detected intents: " + ", ".join(f"{i['intent']} ({i['score']})" for i in u.intents))
    else:
        lines.append("- Detected intents: none with confidence. Answer generally from group data.")
    if u.members:
        lines.append("- Members matched: " + ", ".join(f"#{m['id']} {m['name']}" for m in u.members))
    if u.amounts:
        lines.append("- Amounts mentioned: " + ", ".join(format_ugx(a) for a in u.amounts))
    if u.percents:
        lines.append("- Percents mentioned: " + ", ".join(f"{p:g}%" for p in u.percents))
    if u.facts:
        lines.append(f"- Computed facts: {u.facts}")
    if u.exact_answer:
        lines.append(f"- Exact computed answer: {u.exact_answer}")
        lines.append("- Repeat the computed UGX figures exactly. Do not round differently. Do not invent extra members.")
    for note in u.notes:
        lines.append(f"- Note: {note}")
    return "\n".join(lines)


def variant_index_size() -> int:
    return len(VARIANT_INDEX)


def chosen_intent(u: Understanding) -> str:
    if u.intents:
        # exact_answer may have been computed for a remapped intent
        if u.facts and u.intents[0]["intent"] != "group_health":
            return u.intents[0]["intent"]
        return u.intents[0]["intent"]
    if u.members:
        return "member_status"
    return "group_health"


def attach_patterns(u: Understanding, brief: Any | None) -> Understanding:
    """Fold pattern-engine speech into the understanding without changing math."""
    if brief is None:
        return u
    intent = chosen_intent(u)
    try:
        from SAVIO_patterns import patterns_for_intent, spoken_for_intent, scorecard_for_member
    except ImportError:
        return u

    extra = spoken_for_intent(brief, intent)
    if extra and intent in {
        "group_health", "overexposed", "inactive_members", "loan_exposure",
        "savings_trend", "recommendations", "risk_register", "concentration",
    }:
        if extra not in u.exact_answer:
            u.exact_answer = extra if not u.exact_answer else u.exact_answer
            u.notes.append("Pattern brief applied.")
    if u.members:
        card = scorecard_for_member(brief, u.members[0]["id"])
        if card:
            u.facts = dict(u.facts or {})
            u.facts["risk_score"] = card.risk_score
            u.facts["risk_label"] = card.risk_label
            u.facts["concerns"] = card.concerns
            u.facts["strengths"] = card.strengths
    related = patterns_for_intent(brief, intent)
    if related:
        u.facts = dict(u.facts or {})
        u.facts["pattern_ids"] = [p.id for p in related[:6]]
    return u


def local_reply(u: Understanding) -> str:
    """Fallback used when Gemini is offline. Still professional."""
    text = (u.exact_answer or "").strip()
    if not text:
        if u.members:
            text = f"I matched {u.members[0]['name']}, but I need a clearer question — savings, loan, fines, or status?"
        else:
            text = "Ask me about savings, loans, who is quiet this week, or how the group looks."
    if u.notes:
        # Do not dump internal notes to the user.
        pass
    return text


if __name__ == "__main__":
    result = verify_database()
    # Never print connection URLs or keys.
    print("engine:", result.get("engine"))
    print("ok:", result.get("ok"))
    print("members:", result.get("member_count"))
    print("savings:", result.get("savings_total"))
    print("loans:", result.get("loan_rows"))
    print("interest_math:", result.get("interest_math"))


# End of SAVIO_brain.py
