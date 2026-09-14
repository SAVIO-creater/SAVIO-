"""
SAVIO Playbook
==============
Grounded operating knowledge for a small savings group. This is not
legal advice and not a bank manual. It is the kind of discipline a
good treasurer already knows, written so the assistant can explain
a recommendation instead of only naming a ratio.

When Gemini is asked 'what should we do', it should prefer a playbook
move that matches a live pattern id.
"""

from __future__ import annotations

from typing import Any


PLAYS: list[dict[str, Any]] = [
    {
        "id": "pause_lending",
        "title": "Pause new loans",
        "when": ["liq.negative_principal", "credit.group_exposure_high", "liq.interest_uncovered"],
        "steps": [
            "Announce a temporary freeze on new loans at the next sitting.",
            "Finish collecting agreed repayments first.",
            "Re-open lending only when savings cover loans with a buffer the group has named in advance.",
        ],
        "say": "Stop adding loans until cash is clearly larger than what is already out.",
    },
    {
        "id": "call_quiet_borrowers",
        "title": "Call quiet borrowers first",
        "when": ["combo.quiet_overexposed", "combo.quiet_borrower", "combo.negative_quiet"],
        "steps": [
            "List every borrower who missed this week's deposit.",
            "Call them the same day — not as a threat, as a reminder that the group noticed.",
            "Agree a date for the next payment and write it in the book.",
        ],
        "say": "A borrower who goes quiet is the first person to call, not the last.",
    },
    {
        "id": "cap_one_borrower",
        "title": "Cap any one loan",
        "when": ["credit.concentration_borrower", "conc.loans_topheavy", "member.biggest_loan"],
        "steps": [
            "Agree a simple cap: no member holds more than one third of all loans.",
            "Do not top up a loan that already sits near the cap.",
            "Ask for a surety who is not already stretched.",
        ],
        "say": "One large default can wipe a small group's year. Spread the book.",
    },
    {
        "id": "protect_buffer",
        "title": "Name a cash buffer",
        "when": ["liq.thin_buffer", "liq.healthy", "credit.group_exposure_moderate"],
        "steps": [
            "Write down a buffer — many groups keep the last 20 to 30 percent of savings unlendable.",
            "Count interest due as a claim on that cash, not as a surprise.",
            "Review the buffer every share-out cycle.",
        ],
        "say": "A buffer is the difference between a group and a pile of IOUs.",
    },
    {
        "id": "restart_meetings",
        "title": "Restart the meeting rhythm",
        "when": ["part.all_quiet", "growth.no_recent", "part.many_quiet"],
        "steps": [
            "Pick the next meeting day and send one reminder, not five.",
            "Keep the sitting short: roll call, deposits, loans, fines, close.",
            "Record every shilling before people stand up.",
        ],
        "say": "Groups die between meetings, not during them.",
    },
    {
        "id": "public_fine_register",
        "title": "Keep fines in the open",
        "when": ["disc.heavy_fines", "disc.some_fines", "combo.fined_overexposed", "disc.profit_from_fines"],
        "steps": [
            "Read fines aloud at the meeting so they stay a rule, not a rumour.",
            "Collect the fine or write a date. An uncollected fine is a second loan.",
            "If profit is mostly fines, fix attendance — do not celebrate the penalty pot.",
        ],
        "say": "Fines only work when everyone sees the same register.",
    },
    {
        "id": "thank_savers",
        "title": "Name the savers out loud",
        "when": ["member.top_saver", "growth.month_up", "part.full", "combo.core_healthy"],
        "steps": [
            "Thank the leading savers in the meeting.",
            "Do not turn thanks into pressure to lend more.",
            "Invite smaller savers to a standing order they can keep.",
        ],
        "say": "People repeat what the room applauds.",
    },
    {
        "id": "ringfence_welfare",
        "title": "Keep welfare separate",
        "when": ["profit.welfare_larger", "profit.mix"],
        "steps": [
            "Welfare is for agreed emergencies, not for plugging a loan hole.",
            "Record welfare on its own line so it cannot vanish into cash-on-hand stories.",
            "Write the three cases welfare may be used for.",
        ],
        "say": "Mixing welfare and loans is how arguments start at funerals.",
    },
    {
        "id": "write_the_surety",
        "title": "Write the surety's name",
        "when": ["gov.loan_no_surety", "gov.exited_with_loan"],
        "steps": [
            "No name, no loan — even if everyone in the room 'knows'.",
            "A member who has left still needs a plan for any open loan.",
            "Update status and the loan line in the same sitting.",
        ],
        "say": "Memory is not collateral.",
    },
    {
        "id": "check_anchor",
        "title": "Check on the quiet anchor saver",
        "when": ["combo.anchor_quiet", "conc.savings_topheavy"],
        "steps": [
            "A large saver who misses a week may be travelling, angry, or done.",
            "One private call beats a public interrogation.",
            "If they are leaving, plan liquidity before the next loan is approved.",
        ],
        "say": "When the biggest saver goes quiet, treat it as a cash event.",
    },
    {
        "id": "honest_books",
        "title": "Record the same day",
        "when": ["liq.empty", "part.never_saved", "gov.missing_phone"],
        "steps": [
            "A book updated days later is a story, not a ledger.",
            "Phone numbers exist so reminders do not depend on who walked past whose shop.",
            "Zero savings after months usually means the person is not actually in the group.",
        ],
        "say": "SAVIO can only watch what you write down.",
    },
]


PRINCIPLES = [
    "Record on the day. Yesterday's memory is how shillings disappear.",
    "Savings first, loans second. A group that lends faster than it saves is a queue of disappointments.",
    "One member should not be the bank. Concentration is hidden risk.",
    "Interest is not profit until it is paid. Unpaid interest is hope.",
    "Fines are for discipline, not for income.",
    "Welfare is sacred. Do not raid it to look liquid.",
    "A quiet borrower is a louder signal than a loud meeting.",
    "Share-out is a ceremony. Arrive with numbers that already add up.",
    "Rules that live only in someone's head will be rewritten in anger.",
    "Kindness and collection can sit in the same sentence.",
]


MEETING_AGENDA = [
    "Open and roll call (who is present, who sent word).",
    "Read last meeting's cash position in one minute.",
    "Collect savings. Write each name and amount.",
    "Collect welfare and fines if the rules say so.",
    "Collect loan repayments. Apply them to the book immediately.",
    "Look at members who are quiet and over-borrowed before any new loan.",
    "Hear new loan requests against the cash buffer, not against sympathy.",
    "Confirm surety names out loud.",
    "Agree the next sitting. Close while the book still matches the box.",
]


SHAREOUT_CHECKS = [
    "Every member's savings total matches the deposit register.",
    "Every loan is either cleared or written as still outstanding.",
    "Interest money and fines are listed separately from savings.",
    "Welfare is not mixed into the share-out unless the constitution says so.",
    "The physical cash was counted by two people.",
    "The remainder after equal shares is recorded, not pocketed.",
]


LOAN_QUESTIONS = [
    "Has this member been depositing recently?",
    "Is the requested amount larger than their own savings?",
    "What share of the whole loan book would they then hold?",
    "Who is the surety, and are they already stretched?",
    "When is the first repayment, and can they point to the cash that will pay it?",
    "If they default, what happens to the group's buffer?",
]


def plays_for_pattern_ids(pattern_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(pattern_ids)
    out = []
    for play in PLAYS:
        if wanted.intersection(play["when"]):
            out.append(play)
    return out


def format_playbook_for_prompt(pattern_ids: list[str], limit: int = 3) -> str:
    plays = plays_for_pattern_ids(pattern_ids)[:limit]
    if not plays:
        return (
            "PLAYBOOK:\n"
            "- No special play is forced. Stay with the principles: "
            + " ".join(PRINCIPLES[:3])
        )
    lines = ["PLAYBOOK (use these moves; do not invent a different procedure):"]
    for play in plays:
        lines.append(f"- {play['title']}: {play['say']}")
        for step in play["steps"]:
            lines.append(f"    • {step}")
    lines.append("- Principles that still apply: " + " | ".join(PRINCIPLES[:4]))
    return "\n".join(lines)


def format_meeting_help() -> str:
    numbered = " ".join(f"{i}. {item}" for i, item in enumerate(MEETING_AGENDA, 1))
    return "A clean sitting runs like this: " + numbered


def format_shareout_help() -> str:
    return "Before share-out, check: " + " ".join(SHAREOUT_CHECKS)


def format_loan_help() -> str:
    return "Before approving a loan, ask: " + " ".join(LOAN_QUESTIONS)
