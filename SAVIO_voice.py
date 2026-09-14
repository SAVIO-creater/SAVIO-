"""
SAVIO Voice
===========
Turns computed facts and named patterns into short, professional
replies that fit a phone chat bubble.

The model still writes the final sentence. This layer:
  * decides tone (calm, alert, encouraging)
  * supplies a grounded draft the model should not contradict
  * offers one follow-up question so the chat feels alive
  * resolves 'what about him', 'and loans', 'do that again'
    against the last understanding
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


CALM = "calm"
ALERT = "alert"
WARM = "warm"
PLAIN = "plain"

TONE_BY_SEVERITY = {
    "critical": ALERT,
    "high": ALERT,
    "watch": CALM,
    "info": PLAIN,
    "good": WARM,
}


FOLLOW_UP_BANK = {
    "total_savings": "Want the top savers as well?",
    "total_loans": "Should I show who owes the most?",
    "group_balance": "Do you want that after interest too?",
    "total_profit": "Want that split per member?",
    "inactive_members": "Should I also flag who among them has a loan?",
    "overexposed": "Want a one-line plan for the riskiest member?",
    "group_health": "Want me to start with the riskiest member?",
    "top_savers": "Compare them to the biggest borrowers?",
    "savings_trend": "Shall I put that next to loan exposure?",
    "member_savings": "Want their loan and net position too?",
    "member_loan": "Want the what-if if they paid something today?",
    "member_status": "Want a suggested talking point for the next meeting?",
}


GREETINGS = {
    "hi", "hello", "hey", "hallo", "howdy", "yo", "sup",
    "good morning", "good afternoon", "good evening",
    "morning", "evening", "webale", "oli otya", "jambo",
}

THANKS = {"thanks", "thank you", "ty", "webale", "asante", "appreciate"}

HELP_HINTS = {
    "help", "what can you do", "what do you do", "commands",
    "how do i ask", "examples", "menu",
}

TERM_GLOSSARY = {
    "chama": "A chama is a savings group. Members meet, deposit, and sometimes lend to each other.",
    "vsla": "A VSLA is a Village Savings and Loan Association — a self-managed savings group with a written constitution.",
    "sacco": "A SACCO is a Savings and Credit Cooperative. It is usually larger and more formal than a chama.",
    "shareout": "Share-out is when the group divides savings and profit among members at the end of a cycle.",
    "surety": "A surety is the person who stands in for a borrower if that borrower cannot repay.",
    "welfare": "Welfare is a side pot for emergencies and social support. It is not the loan fund.",
    "fine": "A fine is a penalty the group agreed in its rules — late coming, missed meeting, missed deposit.",
    "exposure": "Loan exposure is how large the loan book is compared with savings. High exposure means less cash buffer.",
    "overexposed": "A member is over-exposed when their loan is bigger than their own savings.",
    "principal": "Principal is the loan amount before interest.",
    "interest": "Interest is the extra money a borrower pays for using the group's cash.",
    "net position": "Net position here is savings minus loan principal. Negative means they owe more than they have put in.",
    "inactive": "Inactive in SAVIO means no deposit recorded in the last 7 days.",
}


# ---------------------------------------------------------------------------
# Conversation memory (process-local; enough for a running server)
# ---------------------------------------------------------------------------

@dataclass
class TurnMemory:
    question: str = ""
    intent: str = ""
    member_ids: list[int] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)
    amounts: list[int] = field(default_factory=list)
    exact_answer: str = ""


_LAST: dict[str, TurnMemory] = {}
_DEFAULT_SESSION = "default"


def remember(session_id: str, memory: TurnMemory) -> None:
    _LAST[session_id or _DEFAULT_SESSION] = memory


def recall(session_id: str) -> TurnMemory | None:
    return _LAST.get(session_id or _DEFAULT_SESSION)


def reset_memory(session_id: str | None = None) -> None:
    if session_id:
        _LAST.pop(session_id, None)
    else:
        _LAST.clear()


PRONOUNS = re.compile(
    r"\b(he|she|him|her|his|they|them|that one|same person|this member|the member)\b",
    re.I,
)
SHORT_FOLLOW = re.compile(
    r"^(and|also|what about|how about|same for|do loans|the loan|savings\??|loans\??|fines\??|why|explain)$",
    re.I,
)


def looks_like_follow_up(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if PRONOUNS.search(q):
        return True
    if len(q.split()) <= 5 and SHORT_FOLLOW.search(q):
        return True
    low = q.lower().rstrip("?.!")
    if low.startswith(("and ", "also ", "what about ", "how about ")) and len(q.split()) <= 5:
        return True
    if q.lower() in {"again", "repeat", "go on", "continue", "more", "and then"}:
        return True
    return False


def merge_follow_up(question: str, previous: TurnMemory | None) -> str:
    """Expand a short follow-up using the last resolved member/intent."""
    if not previous:
        return question
    q = question.strip()
    if not looks_like_follow_up(q):
        return question
    name = previous.member_names[0] if previous.member_names else ""
    if PRONOUNS.search(q) and name:
        q = PRONOUNS.sub(name, q, count=1)
        return q
    low = q.lower().rstrip("?.!")
    if low in {"savings", "and savings", "what about savings"} and name:
        return f"how much has {name} saved"
    if low in {"loans", "loan", "and loans", "what about loans", "the loan"} and name:
        return f"how much does {name} owe"
    if low in {"fines", "and fines"} and name:
        return f"fines of {name}"
    if low in {"why", "explain"} and previous.exact_answer:
        return f"explain this in simple words: {previous.exact_answer}"
    if low in {"again", "repeat"}:
        return previous.question
    if name and len(q.split()) <= 3:
        return f"{q} for {name}"
    return question


# ---------------------------------------------------------------------------
# Meta intents the finance brain should not force into a money figure
# ---------------------------------------------------------------------------

def classify_meta(question: str) -> str | None:
    folded = re.sub(r"[^a-z\s]", " ", (question or "").lower())
    folded = re.sub(r"\s+", " ", folded).strip()
    if not folded:
        return "empty"
    if folded in GREETINGS or folded.startswith("good morning") or folded.startswith("good evening"):
        return "greeting"
    if folded in THANKS or folded.startswith("thank"):
        return "thanks"
    if any(h in folded for h in HELP_HINTS):
        return "help"
    for term, _ in TERM_GLOSSARY.items():
        if folded == term or folded in {f"what is {term}", f"what's {term}", f"define {term}", f"meaning of {term}"}:
            return "glossary"
    if folded.startswith("what is ") or folded.startswith("what's ") or folded.startswith("define "):
        return "glossary"
    return None


def glossary_answer(question: str) -> str | None:
    folded = (question or "").lower()
    for term, meaning in TERM_GLOSSARY.items():
        if term in folded:
            return meaning
    return None


def greeting_answer(snapshot_totals: dict[str, Any] | None = None) -> str:
    if snapshot_totals and snapshot_totals.get("member_count"):
        return (
            "Hello — SAVIO is here. Ask me about savings, loans, who is quiet this week, "
            "or just say 'how is the group'."
        )
    return "Hello — SAVIO is here. Add a few members and deposits, then ask me how the group looks."


def help_answer() -> str:
    return (
        "You can ask things like: how much have we saved, who owes the most, "
        "who has not deposited, how is John, if Mary pays 50,000 what is left, "
        "or give me a health report. Messy spelling is fine."
    )


def thanks_answer() -> str:
    return "Glad to help. I will be here when the next figure comes in."


# ---------------------------------------------------------------------------
# Draft composer
# ---------------------------------------------------------------------------

def pick_tone(patterns: list[dict[str, Any]] | None, intent: str) -> str:
    figure_only = {
        "total_savings", "total_loans", "total_welfare", "total_fines",
        "total_profit", "member_count", "member_savings", "member_loan",
        "member_fines", "member_welfare", "member_payments", "member_interest",
        "what_if_payment", "what_if_deposit", "interest_calc", "share_each",
        "top_savers", "list_members",
    }
    if intent in figure_only:
        return PLAIN
    if not patterns:
        if intent in {"group_health", "overexposed", "inactive_members"}:
            return CALM
        return PLAIN
    worst = "info"
    order = {"critical": 4, "high": 3, "watch": 2, "info": 1, "good": 0}
    for p in patterns:
        sev = p.get("severity", "info")
        if order.get(sev, 0) > order.get(worst, 0):
            worst = sev
    return TONE_BY_SEVERITY.get(worst, PLAIN)


def compose_draft(
    *,
    exact_answer: str,
    intent: str,
    patterns: list[dict[str, Any]] | None = None,
    recommendations: list[str] | None = None,
    member_name: str | None = None,
) -> dict[str, Any]:
    tone = pick_tone(patterns, intent)
    follow = FOLLOW_UP_BANK.get(intent, "Want the group health next?")
    if member_name and intent.startswith("member_"):
        follow = f"Should I compare {member_name} with the group average?"

    opener = {
        ALERT: "This one needs attention. ",
        WARM: "Good news first. ",
        CALM: "",
        PLAIN: "",
    }[tone]

    extra = ""
    related_intents = {
        "group_health", "overexposed", "inactive_members", "loan_exposure",
        "recommendations", "risk_register", "savings_trend",
    }
    if patterns and intent in related_intents:
        top = patterns[0]
        if top.get("severity") in {"critical", "high"} and top.get("headline"):
            if top["headline"] not in (exact_answer or ""):
                extra = " " + top["headline"].rstrip(".") + "."

    rec = ""
    if recommendations and tone in {ALERT, CALM} and intent in {
        "group_health", "overexposed", "loan_exposure", "inactive_members", "savings_trend"
    }:
        rec = " Next step: " + recommendations[0]

    draft = (opener + (exact_answer or "").strip() + extra + rec).strip()
    return {
        "tone": tone,
        "draft": draft,
        "follow_up": follow,
        "style_notes": style_notes(tone),
    }


def style_notes(tone: str) -> str:
    base = (
        "Write like a calm treasurer sitting next to the chairperson. "
        "Short sentences. No slang that a village meeting would not use. "
        "Never invent a number. Never invent a member. "
        "UGX with thousands separators. One follow-up question at the end is enough."
    )
    if tone == ALERT:
        return base + " Be direct. Do not soften a real cash problem."
    if tone == WARM:
        return base + " You may sound pleased, but stay factual."
    return base


def format_voice_for_prompt(draft: dict[str, Any]) -> str:
    return (
        "VOICE LAYER:\n"
        f"- Tone: {draft['tone']}\n"
        f"- Grounded draft (you may polish wording, not figures): {draft['draft']}\n"
        f"- Offer this follow-up if it still fits: {draft['follow_up']}\n"
        f"- Style: {draft['style_notes']}"
    )


SYSTEM_SAVIO_CORE = (
    "You are SAVIO AI, the in-house assistant for a savings group (chama / VSLA) app. "
    "You work with a Python brain that has already read the question, fixed typos, "
    "matched members, and computed every shilling. A pattern engine has already "
    "named the risks. Your job is to sound like a careful human treasurer, not a dashboard.\n"
    "Rules you cannot break:\n"
    "1. Use the computed UGX figures exactly. Do not round them a different way.\n"
    "2. Do not invent members, meetings, dates, or amounts.\n"
    "3. If the brain says the answer is empty or unknown, say so.\n"
    "4. Prefer the combined pattern ('quiet and over-borrowed') over a list of raw rows.\n"
    "5. Keep it under 120 words unless the user asked for a report.\n"
    "6. End with at most one short question if it helps the chairperson act.\n"
    "7. English is default. If the user writes in Luganda, Runyankole, Swahili or informal spelling, answer in clear simple English unless they asked otherwise.\n"
)

SYSTEM_GEMINI_CORE = (
    "You are Gemini, sitting beside SAVIO inside a savings-group app. "
    "You may explain ideas (interest, share-out, constitutions, meeting discipline) "
    "and you may use the group numbers when they help. You still must not invent "
    "group figures. If the question is not about this group, set the snapshot aside."
)
