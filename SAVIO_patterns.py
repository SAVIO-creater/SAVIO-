"""
SAVIO Pattern Engine
====================
Reads the same snapshot the Brain uses and turns raw rows into
professional, explainable signals a treasurer can act on.

This is not a machine-learning black box. Every flag is a named
rule with a severity, a plain-English headline, the exact figures
that triggered it, and a recommended next step.

Design rules
------------
1. Never invent a member or an amount. If the snapshot is empty,
   return an empty brief.
2. Money stays in whole UGX. Ratios are rounded for speech, not
   for storage.
3. A pattern that is true of one person and also of the group
   is emitted once at each level so the assistant can choose.
4. Combined patterns beat isolated ones. "Quiet AND over-borrowed"
   is a different risk from either fact alone.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

try:
    from Python_Brain import (
        format_ugx,
        interest_on,
        load_snapshot,
        pct,
        ratio,
        ugx,
    )
except ImportError:
    # Fallback copies so this module can be imported on its own
    # during tests. The live server always has Python_Brain.
    def ugx(value: Any) -> int:
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

    def format_ugx(value: Any) -> str:
        return f"UGX {ugx(value):,}"

    def interest_on(principal: Any, rate_percent: Any) -> int:
        p = float(principal or 0)
        r = float(rate_percent or 0)
        if p <= 0 or r <= 0:
            return 0
        return ugx(p * r / 100.0)

    def load_snapshot(db_path: str = "SAVIO-database/SAVIO.db") -> dict[str, Any]:
        raise RuntimeError("Python_Brain.load_snapshot is required")


# ---------------------------------------------------------------------------
# Severity and taxonomy
# ---------------------------------------------------------------------------

SEVERITY_ORDER = {"critical": 4, "high": 3, "watch": 2, "info": 1, "good": 0}

PATTERN_FAMILIES = (
    "liquidity",
    "credit_risk",
    "participation",
    "concentration",
    "discipline",
    "growth",
    "profit",
    "member_health",
    "governance",
    "combined",
)


@dataclass
class Pattern:
    id: str
    family: str
    severity: str
    headline: str
    detail: str
    action: str
    figures: dict[str, Any] = field(default_factory=dict)
    member_ids: list[int] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)
    score: float = 0.0
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def spoken(self) -> str:
        names = ", ".join(self.member_names[:4])
        extra = f" Involves {names}." if names else ""
        return f"{self.headline} {self.detail}{extra} Next step: {self.action}"


@dataclass
class MemberScorecard:
    id: int
    name: str
    savings: int
    loan_principal: int
    loan_due: int
    loan_rate: float
    fines: int
    welfare: int
    payments: int
    net: int
    inactive_7d: bool
    overexposed: bool
    savings_share: float
    loan_share: float
    own_loan_to_savings: float
    risk_score: int
    risk_label: str
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    watchpoints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatternBrief:
    generated_at: str
    group_health_score: int
    group_health_label: str
    one_line_brief: str
    patterns: list[Pattern]
    scorecards: list[MemberScorecard]
    ratios: dict[str, Any]
    recommendations: list[str]
    follow_ups: list[str]
    spoken_brief: str
    spoken_risks: str
    spoken_goods: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "group_health_score": self.group_health_score,
            "group_health_label": self.group_health_label,
            "one_line_brief": self.one_line_brief,
            "patterns": [p.as_dict() for p in self.patterns],
            "scorecards": [s.as_dict() for s in self.scorecards],
            "ratios": self.ratios,
            "recommendations": self.recommendations,
            "follow_ups": self.follow_ups,
            "spoken_brief": self.spoken_brief,
            "spoken_risks": self.spoken_risks,
            "spoken_goods": self.spoken_goods,
        }


# ---------------------------------------------------------------------------
# Small statistics used by several rules
# ---------------------------------------------------------------------------

def _safe_mean(values: Iterable[float]) -> float:
    data = [float(v) for v in values]
    if not data:
        return 0.0
    return sum(data) / len(data)


def _safe_median(values: Iterable[float]) -> float:
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    mid = len(data) // 2
    if len(data) % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def _herfindahl(shares: Iterable[float]) -> float:
    """HHI on 0..1 shares. 1.0 = one person owns everything."""
    return round(sum(s * s for s in shares if s > 0), 4)


def _gini(values: Iterable[float]) -> float:
    data = sorted(max(0.0, float(v)) for v in values)
    n = len(data)
    if n == 0:
        return 0.0
    total = sum(data)
    if total <= 0:
        return 0.0
    weighted = sum((i + 1) * x for i, x in enumerate(data))
    return round((2.0 * weighted) / (n * total) - (n + 1) / n, 4)


def _concentration_label(hhi: float) -> str:
    if hhi >= 0.45:
        return "highly concentrated"
    if hhi >= 0.25:
        return "moderately concentrated"
    if hhi >= 0.12:
        return "somewhat spread"
    return "well spread"


def _trend_label(new: int, old: int) -> str:
    if old <= 0 and new > 0:
        return "started moving"
    if old <= 0 and new <= 0:
        return "flat"
    change = pct(new - old, old)
    if change >= 25:
        return "rising sharply"
    if change >= 8:
        return "rising"
    if change <= -25:
        return "falling sharply"
    if change <= -8:
        return "falling"
    return "steady"


def _risk_label(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 35:
        return "elevated"
    if score >= 18:
        return "watch"
    return "healthy"


def _health_label(score: int) -> str:
    if score >= 85:
        return "strong"
    if score >= 70:
        return "stable"
    if score >= 55:
        return "mixed"
    if score >= 40:
        return "strained"
    return "fragile"


def _top_n(members: list[dict[str, Any]], key: str, n: int = 3, positive: bool = True) -> list[dict[str, Any]]:
    pool = [m for m in members if (m.get(key, 0) or 0) > 0] if positive else list(members)
    return sorted(pool, key=lambda m: m.get(key, 0) or 0, reverse=True)[:n]


# ---------------------------------------------------------------------------
# Member scorecards
# ---------------------------------------------------------------------------

def build_scorecards(snapshot: dict[str, Any]) -> list[MemberScorecard]:
    members = snapshot.get("members") or []
    totals = snapshot.get("totals") or {}
    total_savings = max(1, ugx(totals.get("savings")))
    total_loans = max(1, ugx(totals.get("loan_principal")))
    cards: list[MemberScorecard] = []

    for m in members:
        savings = ugx(m.get("savings"))
        loan = ugx(m.get("loan_principal"))
        due = ugx(m.get("loan_total_due"))
        fines = ugx(m.get("fines"))
        welfare = ugx(m.get("welfare"))
        payments = ugx(m.get("payments"))
        net = ugx(m.get("net"))
        inactive = bool(m.get("inactive_7d"))
        over = bool(m.get("overexposed"))
        sav_share = ratio(savings, total_savings)
        loan_share = ratio(loan, total_loans) if totals.get("loan_principal") else 0.0
        own_lts = ratio(loan, savings) if savings else (99.0 if loan else 0.0)

        score = 0
        strengths: list[str] = []
        concerns: list[str] = []
        watchpoints: list[str] = []

        if savings > 0 and not loan:
            strengths.append("saving without an open loan")
            score -= 6
        if sav_share >= 0.2 and savings > 0:
            strengths.append("carries a large share of group savings")
        if payments > 0 and loan > 0:
            strengths.append("has started repaying")
            score -= 4
        if welfare > 0:
            strengths.append("contributes to welfare")
        if not inactive and savings > 0:
            strengths.append("deposited in the last 7 days")
            score -= 4

        if over:
            concerns.append("loan is larger than own savings")
            score += 28
        if inactive:
            concerns.append("no deposit in the last 7 days")
            score += 16
        if own_lts >= 1.5 and loan > 0:
            concerns.append("borrowed well beyond personal savings")
            score += 12
        elif own_lts >= 0.8 and loan > 0:
            watchpoints.append("loan is close to personal savings")
            score += 8
        if fines > 0:
            if fines >= 20000:
                concerns.append("carries a heavy fine balance")
                score += 14
            else:
                watchpoints.append("has been fined")
                score += 6
        if loan > 0 and payments == 0:
            watchpoints.append("open loan with no repayment recorded")
            score += 8
        if savings == 0 and loan == 0:
            watchpoints.append("no savings activity yet")
            score += 4
        if net < 0:
            concerns.append("net position is negative")
            score += 10
        if loan_share >= 0.4 and loan > 0:
            concerns.append("holds a large share of the group's loan book")
            score += 10

        score = max(0, min(100, score + 12))  # baseline so empty is not "perfect"
        if not concerns and not watchpoints and savings > 0:
            score = min(score, 15)

        cards.append(
            MemberScorecard(
                id=int(m["id"]),
                name=m.get("name") or f"#{m['id']}",
                savings=savings,
                loan_principal=loan,
                loan_due=due,
                loan_rate=float(m.get("loan_rate") or 0),
                fines=fines,
                welfare=welfare,
                payments=payments,
                net=net,
                inactive_7d=inactive,
                overexposed=over,
                savings_share=sav_share,
                loan_share=loan_share,
                own_loan_to_savings=own_lts,
                risk_score=score,
                risk_label=_risk_label(score),
                strengths=strengths,
                concerns=concerns,
                watchpoints=watchpoints,
            )
        )

    cards.sort(key=lambda c: (c.risk_score, c.loan_principal), reverse=True)
    return cards


# ---------------------------------------------------------------------------
# Individual pattern rules
# ---------------------------------------------------------------------------

def _pattern(
    pid: str,
    family: str,
    severity: str,
    headline: str,
    detail: str,
    action: str,
    figures: dict[str, Any] | None = None,
    members: list[dict[str, Any]] | None = None,
    cards: list[MemberScorecard] | None = None,
    score: float = 0.0,
    tags: list[str] | None = None,
) -> Pattern:
    people = members or []
    scored = cards or []
    ids = [int(m["id"]) for m in people] + [c.id for c in scored]
    names = [m.get("name", "") for m in people] + [c.name for c in scored]
    # unique preserve order
    seen = set()
    clean_ids = []
    clean_names = []
    for i, n in zip(ids, names):
        if i in seen:
            continue
        seen.add(i)
        clean_ids.append(i)
        clean_names.append(n)
    return Pattern(
        id=pid,
        family=family,
        severity=severity,
        headline=headline,
        detail=detail,
        action=action,
        figures=figures or {},
        member_ids=clean_ids,
        member_names=clean_names,
        score=score,
        tags=tags or [],
    )


def detect_liquidity(snapshot: dict[str, Any]) -> list[Pattern]:
    t = snapshot["totals"]
    out: list[Pattern] = []
    savings = ugx(t["savings"])
    principal = ugx(t["loan_principal"])
    due = ugx(t["loans_with_interest"])
    cash_vs_p = ugx(t["balance_vs_principal"])
    cash_vs_d = ugx(t["balance_vs_due"])
    coverage = ratio(savings, due) if due else 9.99

    if savings == 0 and principal == 0:
        out.append(_pattern(
            "liq.empty", "liquidity", "info",
            "The group book is still empty.",
            "No savings or loans have been recorded yet.",
            "Add members and the first deposits so SAVIO has something to watch.",
            {"savings": 0, "loans": 0},
            score=0.2, tags=["empty", "setup"],
        ))
        return out

    if cash_vs_p < 0:
        out.append(_pattern(
            "liq.negative_principal", "liquidity", "critical",
            "Loans already exceed savings.",
            f"Savings {format_ugx(savings)} sit below loan principal {format_ugx(principal)}. "
            f"Cash after principal is {format_ugx(cash_vs_p)}.",
            "Pause new loans until deposits catch up, and chase the largest outstanding balances first.",
            {"savings": savings, "loan_principal": principal, "gap": cash_vs_p},
            score=0.96, tags=["liquidity", "overextended"],
        ))
    elif cash_vs_d < 0:
        out.append(_pattern(
            "liq.interest_uncovered", "liquidity", "high",
            "Savings cover the principal but not the interest due.",
            f"After principal the group still has {format_ugx(cash_vs_p)}, "
            f"but once interest is reserved the gap is {format_ugx(cash_vs_d)}.",
            "Treat interest as a real claim on cash. Collect it on schedule before issuing more credit.",
            {"balance_vs_principal": cash_vs_p, "balance_vs_due": cash_vs_d, "due": due},
            score=0.78, tags=["liquidity", "interest"],
        ))
    elif coverage < 1.3:
        out.append(_pattern(
            "liq.thin_buffer", "liquidity", "watch",
            "The cash buffer above loans is thin.",
            f"Savings {format_ugx(savings)} cover loans-with-interest {format_ugx(due)} "
            f"only {coverage:.2f} times.",
            "Keep a simple rule: do not lend the last 30 percent of group savings.",
            {"coverage": coverage, "savings": savings, "due": due},
            score=0.55, tags=["liquidity", "buffer"],
        ))
    else:
        out.append(_pattern(
            "liq.healthy", "liquidity", "good",
            "Cash still covers the loan book with room to spare.",
            f"Savings {format_ugx(savings)} against {format_ugx(due)} due "
            f"(coverage {coverage:.2f}). Balance after principal is {format_ugx(cash_vs_p)}.",
            "Protect this buffer. New loans should stay well inside it.",
            {"coverage": coverage, "balance_vs_principal": cash_vs_p},
            score=0.3, tags=["liquidity", "healthy"],
        ))
    return out


def detect_credit_risk(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    t = snapshot["totals"]
    members = snapshot["members"]
    out: list[Pattern] = []
    over = [c for c in cards if c.overexposed]
    high_own = [c for c in cards if c.loan_principal > 0 and c.own_loan_to_savings >= 0.8]
    no_pay = [c for c in cards if c.loan_principal > 0 and c.payments == 0]
    heavy = [c for c in cards if c.loan_share >= 0.35 and c.loan_principal > 0]
    exposure = float(t.get("loan_to_savings_ratio") or 0)

    if over:
        sev = "critical" if len(over) >= 3 or any(c.own_loan_to_savings >= 2 for c in over) else "high"
        names = "; ".join(
            f"{c.name} owes {format_ugx(c.loan_principal)} on {format_ugx(c.savings)} saved"
            for c in over[:5]
        )
        out.append(_pattern(
            "credit.overexposed", "credit_risk", sev,
            f"{len(over)} member(s) borrowed more than they have saved.",
            names + ".",
            "Ask each of them for a repayment plan this week, and do not top up those loans.",
            {"count": len(over)},
            cards=over,
            score=0.9 if sev == "critical" else 0.8,
            tags=["overexposed", "default-risk"],
        ))

    if heavy:
        out.append(_pattern(
            "credit.concentration_borrower", "credit_risk", "high",
            "A small number of members hold a large share of the loan book.",
            "; ".join(f"{c.name} {pct(c.loan_share, 1):.0f}%" for c in heavy) + ".",
            "Cap any one member at about a third of the loan book.",
            {"borrowers": [{"name": c.name, "share": c.loan_share} for c in heavy]},
            cards=heavy,
            score=0.74, tags=["concentration", "credit"],
        ))

    if no_pay and t.get("loan_principal", 0) > 0:
        out.append(_pattern(
            "credit.no_repayment_yet", "credit_risk", "watch",
            f"{len(no_pay)} open loan(s) have no repayment recorded.",
            ", ".join(c.name for c in no_pay[:6]) + ".",
            "Confirm whether repayments happen off-book. If not, start collecting this cycle.",
            {"count": len(no_pay)},
            cards=no_pay,
            score=0.58, tags=["repayment", "discipline"],
        ))

    if exposure >= 0.7:
        out.append(_pattern(
            "credit.group_exposure_high", "credit_risk", "high",
            "Group loan exposure is high.",
            f"Loans including interest are {format_ugx(t['loans_with_interest'])} "
            f"against savings {format_ugx(t['savings'])} (ratio {exposure:.2f}, {t.get('exposure_label')}).",
            "Freeze new lending except for emergency cases until the ratio falls under 0.6.",
            {"ratio": exposure, "label": t.get("exposure_label")},
            score=0.82, tags=["exposure"],
        ))
    elif exposure >= 0.3:
        out.append(_pattern(
            "credit.group_exposure_moderate", "credit_risk", "watch",
            "Group loan exposure is moderate.",
            f"Ratio {exposure:.2f} — {t.get('exposure_label')}.",
            "Keep approving loans against a written cap, not against hope.",
            {"ratio": exposure},
            score=0.45, tags=["exposure"],
        ))
    elif t.get("loan_principal", 0) > 0:
        out.append(_pattern(
            "credit.group_exposure_low", "credit_risk", "good",
            "Lending is still a small share of savings.",
            f"Ratio {exposure:.2f} — {t.get('exposure_label')}.",
            "You have room to lend, but only to members who are current on deposits.",
            {"ratio": exposure},
            score=0.25, tags=["exposure", "healthy"],
        ))

    high_rate = [m for m in members if (m.get("loan_rate") or 0) >= 20 and (m.get("loan_principal") or 0) > 0]
    if high_rate:
        out.append(_pattern(
            "credit.steep_rate", "credit_risk", "info",
            "Some loans carry a steep interest rate.",
            "; ".join(f"{m['name']} {m['loan_rate']:g}%" for m in high_rate[:5]) + ".",
            "Check that the rate was agreed in the meeting and written down.",
            cards=None,
            members=high_rate,
            score=0.35, tags=["interest", "governance"],
        ))
    return out


def detect_participation(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    t = snapshot["totals"]
    out: list[Pattern] = []
    count = int(t.get("member_count") or 0)
    inactive = [c for c in cards if c.inactive_7d]
    silent_zero = [c for c in cards if c.savings == 0]
    active = [c for c in cards if not c.inactive_7d]
    participation = pct(len(active), count) if count else 0.0

    if count == 0:
        return out

    if len(inactive) == count:
        out.append(_pattern(
            "part.all_quiet", "participation", "critical",
            "Nobody has deposited in the last 7 days.",
            f"All {count} members are quiet.",
            "Call a short meeting. A group that stops meeting stops existing.",
            {"inactive": count, "members": count},
            cards=inactive,
            score=0.95, tags=["inactive", "meeting"],
        ))
    elif len(inactive) >= max(2, math.ceil(count * 0.4)):
        out.append(_pattern(
            "part.many_quiet", "participation", "high",
            f"{len(inactive)} of {count} members missed deposits this week.",
            "Quiet: " + ", ".join(c.name for c in inactive[:8]) + ".",
            "Send a reminder today and record who responds. Persistent silence is a risk signal.",
            {"inactive": len(inactive), "participation_pct": participation},
            cards=inactive,
            score=0.8, tags=["inactive"],
        ))
    elif inactive:
        out.append(_pattern(
            "part.some_quiet", "participation", "watch",
            f"{len(inactive)} member(s) skipped this week.",
            ", ".join(c.name for c in inactive) + ".",
            "A single quiet week is normal. Two in a row is a conversation.",
            {"inactive": len(inactive)},
            cards=inactive,
            score=0.48, tags=["inactive"],
        ))
    else:
        out.append(_pattern(
            "part.full", "participation", "good",
            "Every member deposited in the last 7 days.",
            f"Participation is 100% across {count} members.",
            "Acknowledge it in the next meeting. Consistency is the product.",
            {"participation_pct": 100.0},
            score=0.28, tags=["participation", "healthy"],
        ))

    if silent_zero and len(silent_zero) < count:
        out.append(_pattern(
            "part.never_saved", "participation", "watch",
            f"{len(silent_zero)} member(s) still show zero savings.",
            ", ".join(c.name for c in silent_zero[:8]) + ".",
            "Confirm they are active members. If not, update their status so the books stay honest.",
            {"count": len(silent_zero)},
            cards=silent_zero,
            score=0.42, tags=["zero-savings"],
        ))
    return out


def detect_concentration(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    t = snapshot["totals"]
    out: list[Pattern] = []
    if not cards or not t.get("savings"):
        return out

    sav_shares = [c.savings_share for c in cards]
    loan_shares = [c.loan_share for c in cards if c.loan_principal > 0]
    sav_hhi = _herfindahl(sav_shares)
    loan_hhi = _herfindahl(loan_shares) if loan_shares else 0.0
    sav_gini = _gini([c.savings for c in cards])
    top_saver = max(cards, key=lambda c: c.savings)
    top3_sav = sum(c.savings for c in sorted(cards, key=lambda x: x.savings, reverse=True)[:3])
    top3_share = ratio(top3_sav, t["savings"])

    if sav_hhi >= 0.35 or top3_share >= 0.75:
        out.append(_pattern(
            "conc.savings_topheavy", "concentration", "watch",
            "Group savings sit with a few members.",
            f"Top 3 hold {pct(top3_share, 1):.0f}% of savings. "
            f"Concentration is {_concentration_label(sav_hhi)} (HHI {sav_hhi:.2f}, Gini {sav_gini:.2f}). "
            f"Largest saver is {top_saver.name} at {format_ugx(top_saver.savings)}.",
            "Celebrate the big savers, but recruit more regular small deposits so one absence does not freeze the group.",
            {"hhi": sav_hhi, "gini": sav_gini, "top3_share": top3_share},
            cards=[top_saver],
            score=0.6, tags=["concentration", "savings"],
        ))
    else:
        out.append(_pattern(
            "conc.savings_spread", "concentration", "good",
            "Savings are reasonably spread across members.",
            f"Top 3 hold {pct(top3_share, 1):.0f}%. HHI {sav_hhi:.2f} ({_concentration_label(sav_hhi)}).",
            "Keep encouraging the smaller savers so the spread holds.",
            {"hhi": sav_hhi, "top3_share": top3_share},
            score=0.22, tags=["concentration", "healthy"],
        ))

    if loan_hhi >= 0.4 and loan_shares:
        out.append(_pattern(
            "conc.loans_topheavy", "concentration", "high",
            "The loan book is concentrated.",
            f"Loan HHI {loan_hhi:.2f} ({_concentration_label(loan_hhi)}).",
            "A default by one large borrower would bruise the whole group. Diversify or collect.",
            {"loan_hhi": loan_hhi},
            score=0.72, tags=["concentration", "loans"],
        ))
    return out


def detect_discipline(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    t = snapshot["totals"]
    out: list[Pattern] = []
    fined = [c for c in cards if c.fines > 0]
    heavy_fines = [c for c in cards if c.fines >= 20000]
    profit = ugx(t.get("profit"))
    fine_share = ratio(t.get("fines"), profit) if profit else 0.0

    if heavy_fines:
        out.append(_pattern(
            "disc.heavy_fines", "discipline", "high",
            "A few members carry heavy fines.",
            "; ".join(f"{c.name} {format_ugx(c.fines)}" for c in heavy_fines[:5]) + ".",
            "Fines only work if they are collected and the behaviour changes. Follow up in person.",
            {"count": len(heavy_fines)},
            cards=heavy_fines,
            score=0.7, tags=["fines"],
        ))
    elif fined:
        out.append(_pattern(
            "disc.some_fines", "discipline", "info",
            f"{len(fined)} member(s) have fines on the book.",
            "; ".join(f"{c.name} {format_ugx(c.fines)}" for c in fined[:6]) + ".",
            "Keep the fine register public in the meeting. Quiet ledgers grow arguments.",
            {"count": len(fined), "total": t.get("fines")},
            cards=fined,
            score=0.36, tags=["fines"],
        ))
    else:
        out.append(_pattern(
            "disc.clean_fines", "discipline", "good",
            "No fines are sitting on the book.",
            "Either the group is punctual, or fines are not being recorded.",
            "If rules exist, record the next breach the same day so the register stays trusted.",
            {"total_fines": 0},
            score=0.2, tags=["fines", "healthy"],
        ))

    if profit > 0 and fine_share >= 0.6:
        out.append(_pattern(
            "disc.profit_from_fines", "discipline", "watch",
            "Most group 'profit' is coming from fines, not interest.",
            f"Fines {format_ugx(t.get('fines'))} vs interest money {format_ugx(t.get('loan_interest_money'))} "
            f"({pct(fine_share, 1):.0f}% of profit).",
            "A group that lives on penalties is collecting pain, not building a loan book. Fix attendance first.",
            {"fine_share_of_profit": fine_share},
            score=0.62, tags=["profit-mix", "fines"],
        ))
    return out


def detect_growth(snapshot: dict[str, Any]) -> list[Pattern]:
    t = snapshot["totals"]
    trend = snapshot.get("trend") or {}
    out: list[Pattern] = []
    weekly = list(t.get("weekly_trend") or [])
    this_week = ugx(t.get("this_week_savings"))
    last_week = ugx(t.get("last_week_savings"))
    direction = trend.get("direction") or _trend_label(this_week, last_week)

    if len(weekly) >= 4:
        older = weekly[0]
        newer = weekly[-1]
        month_dir = _trend_label(newer, older)
        if newer == 0 and older == 0 and this_week == 0 and last_week == 0:
            out.append(_pattern(
                "growth.no_recent", "growth", "watch",
                "No deposits landed in the last four weeks.",
                "The trend line is flat at zero.",
                "If meetings still happen, the ledger is behind. If meetings stopped, restart them.",
                {"weekly": weekly},
                score=0.68, tags=["trend", "stalled"],
            ))
        elif month_dir in ("falling", "falling sharply"):
            out.append(_pattern(
                "growth.month_down", "growth", "high",
                "Weekly deposits have been falling across the month.",
                f"Four-week series: {', '.join(format_ugx(v) for v in weekly)}. "
                f"This week {format_ugx(this_week)} vs last week {format_ugx(last_week)} ({direction}).",
                "Ask what changed: harvest season, a dispute, or simply forgotten reminders.",
                {"weekly": weekly, "direction": direction},
                score=0.76, tags=["trend", "falling"],
            ))
        elif month_dir in ("rising", "rising sharply"):
            out.append(_pattern(
                "growth.month_up", "growth", "good",
                "Weekly deposits have been rising across the month.",
                f"Four-week series: {', '.join(format_ugx(v) for v in weekly)}. "
                f"This week {format_ugx(this_week)} vs last week {format_ugx(last_week)}.",
                "Lock the habit. Rising weeks disappear quickly if meetings get sloppy.",
                {"weekly": weekly, "direction": direction},
                score=0.34, tags=["trend", "rising"],
            ))
        else:
            out.append(_pattern(
                "growth.month_steady", "growth", "info",
                "Weekly deposits are roughly steady.",
                f"Four-week series: {', '.join(format_ugx(v) for v in weekly)}.",
                "Steady is good. A small push in the meeting can still lift the floor.",
                {"weekly": weekly, "direction": direction},
                score=0.3, tags=["trend"],
            ))
    else:
        change = ugx(trend.get("change"))
        out.append(_pattern(
            "growth.week_compare", "growth", "info",
            f"This week versus last week is {direction}.",
            f"This week {format_ugx(this_week)}, last week {format_ugx(last_week)}, change {format_ugx(change)}.",
            "Watch one more week before calling it a trend.",
            {"this_week": this_week, "last_week": last_week, "change": change},
            score=0.3, tags=["trend"],
        ))
    return out


def detect_profit(snapshot: dict[str, Any]) -> list[Pattern]:
    t = snapshot["totals"]
    out: list[Pattern] = []
    profit = ugx(t.get("profit"))
    interest_money = ugx(t.get("loan_interest_money"))
    fines = ugx(t.get("fines"))
    welfare = ugx(t.get("welfare"))
    members = int(t.get("member_count") or 0)
    per_head = (profit // members) if members else 0

    if profit <= 0 and interest_money <= 0 and fines <= 0:
        out.append(_pattern(
            "profit.none", "profit", "info",
            "The group has not earned interest or fines yet.",
            "Profit is zero.",
            "Profit comes later. First get regular deposits and clean loan records.",
            {"profit": 0},
            score=0.15, tags=["profit"],
        ))
        return out

    out.append(_pattern(
        "profit.mix", "profit", "info",
        "Here is the current profit mix.",
        f"Interest money {format_ugx(interest_money)} + fines {format_ugx(fines)} "
        f"= {format_ugx(profit)}"
        + (f". Split equally that is {format_ugx(per_head)} each, before any remainder." if members else "."),
        "Do not spend profit that is still sitting inside unpaid loans.",
        {
            "interest_money": interest_money,
            "fines": fines,
            "profit": profit,
            "per_member": per_head,
            "welfare": welfare,
        },
        score=0.3, tags=["profit", "share-out"],
    ))

    if welfare > 0 and welfare > profit:
        out.append(_pattern(
            "profit.welfare_larger", "profit", "info",
            "Welfare collected is larger than current profit.",
            f"Welfare {format_ugx(welfare)} vs profit {format_ugx(profit)}.",
            "Keep welfare ring-fenced. It is not a loan pot.",
            {"welfare": welfare, "profit": profit},
            score=0.28, tags=["welfare"],
        ))
    return out


def detect_combined(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    """The patterns that make an assistant feel like it is thinking."""
    out: list[Pattern] = []
    quiet_and_over = [c for c in cards if c.inactive_7d and c.overexposed]
    quiet_and_loan = [c for c in cards if c.inactive_7d and c.loan_principal > 0]
    fined_and_over = [c for c in cards if c.fines > 0 and c.overexposed]
    top_but_quiet = [
        c for c in cards
        if c.inactive_7d and c.savings_share >= 0.15 and c.savings > 0
    ]
    negative_and_quiet = [c for c in cards if c.net < 0 and c.inactive_7d]
    strong = [c for c in cards if c.risk_label == "healthy" and c.savings > 0 and not c.inactive_7d]

    if quiet_and_over:
        out.append(_pattern(
            "combo.quiet_overexposed", "combined", "critical",
            "Some members are both over-borrowed and silent this week.",
            "; ".join(
                f"{c.name} owes {format_ugx(c.loan_principal)} / saved {format_ugx(c.savings)}"
                for c in quiet_and_over
            ) + ". Either fact is a watch. Together it is a collection problem.",
            "Call them before the next meeting. Do not wait for the cycle to roll.",
            {"count": len(quiet_and_over)},
            cards=quiet_and_over,
            score=0.97, tags=["combined", "priority"],
        ))

    if negative_and_quiet and not quiet_and_over:
        out.append(_pattern(
            "combo.negative_quiet", "combined", "high",
            "Negative net position plus a missed deposit.",
            ", ".join(c.name for c in negative_and_quiet) + ".",
            "Treat this as early default risk, not just a skipped week.",
            cards=negative_and_quiet,
            score=0.84, tags=["combined"],
        ))

    if quiet_and_loan and not quiet_and_over:
        out.append(_pattern(
            "combo.quiet_borrower", "combined", "watch",
            "Borrowers who skipped this week's deposit.",
            ", ".join(c.name for c in quiet_and_loan) + ".",
            "A borrower who stops saving is telling you the loan just became harder to collect.",
            cards=quiet_and_loan,
            score=0.66, tags=["combined"],
        ))

    if fined_and_over:
        out.append(_pattern(
            "combo.fined_overexposed", "combined", "high",
            "Over-borrowed members who also carry fines.",
            "; ".join(f"{c.name} fines {format_ugx(c.fines)}" for c in fined_and_over) + ".",
            "Stacking a fine on a strained loan often delays repayment. Collect the loan first.",
            cards=fined_and_over,
            score=0.8, tags=["combined", "fines"],
        ))

    if top_but_quiet:
        out.append(_pattern(
            "combo.anchor_quiet", "combined", "watch",
            "A large saver went quiet this week.",
            ", ".join(f"{c.name} ({pct(c.savings_share, 1):.0f}% of savings)" for c in top_but_quiet) + ".",
            "Check in kindly. If an anchor saver leaves, liquidity gaps appear fast.",
            cards=top_but_quiet,
            score=0.7, tags=["combined", "concentration"],
        ))

    if strong and len(strong) >= max(2, len(cards) // 3):
        out.append(_pattern(
            "combo.core_healthy", "combined", "good",
            "A solid core of members is current and not over-borrowed.",
            ", ".join(c.name for c in strong[:6])
            + ("…" if len(strong) > 6 else ".")
            + f" {len(strong)} in good standing.",
            "Lean on this core for reminders and surety, not for extra loans they do not need.",
            cards=strong[:6],
            score=0.32, tags=["combined", "healthy"],
        ))
    return out


def detect_governance(snapshot: dict[str, Any], cards: list[MemberScorecard]) -> list[Pattern]:
    members = snapshot.get("members") or []
    out: list[Pattern] = []
    missing_phone = [m for m in members if not str(m.get("number") or "").strip()]
    missing_surety = [
        m for m in members
        if (m.get("loan_principal") or 0) > 0 and not str(m.get("surety") or "").strip()
    ]
    inactive_status = [
        m for m in members
        if str(m.get("status") or "").lower() in {"inactive", "left", "exited", "dormant"}
    ]

    if missing_surety:
        out.append(_pattern(
            "gov.loan_no_surety", "governance", "high",
            "Active loans with no surety recorded.",
            ", ".join(m.get("name", "") for m in missing_surety[:8]) + ".",
            "A loan without a named surety is a group loan in disguise. Write the name down.",
            members=missing_surety,
            score=0.73, tags=["surety", "governance"],
        ))

    if missing_phone:
        out.append(_pattern(
            "gov.missing_phone", "governance", "info",
            f"{len(missing_phone)} member record(s) have no phone number.",
            "Reminders and collection calls need a number.",
            "Fill the number on the member profile before the next cycle.",
            members=missing_phone,
            score=0.25, tags=["records"],
        ))

    if inactive_status:
        still_loaned = [m for m in inactive_status if (m.get("loan_principal") or 0) > 0]
        if still_loaned:
            out.append(_pattern(
                "gov.exited_with_loan", "governance", "critical",
                "A member marked inactive or exited still has an open loan.",
                ", ".join(m.get("name", "") for m in still_loaned) + ".",
                "Status and the loan book disagree. Resolve it in the next sitting.",
                members=still_loaned,
                score=0.9, tags=["status", "loan"],
            ))
    return out


def detect_member_highlights(cards: list[MemberScorecard]) -> list[Pattern]:
    out: list[Pattern] = []
    if not cards:
        return out
    savers = [c for c in cards if c.savings > 0]
    borrowers = [c for c in cards if c.loan_principal > 0]
    if savers:
        best = max(savers, key=lambda c: c.savings)
        out.append(_pattern(
            "member.top_saver", "member_health", "good",
            f"{best.name} is the leading saver.",
            f"{format_ugx(best.savings)} ({pct(best.savings_share, 1):.1f}% of the group).",
            "A public thank-you in the meeting costs nothing and keeps the habit visible.",
            cards=[best],
            score=0.24, tags=["top-saver"],
        ))
    if borrowers:
        biggest = max(borrowers, key=lambda c: c.loan_principal)
        out.append(_pattern(
            "member.biggest_loan", "member_health", "info",
            f"{biggest.name} holds the largest loan.",
            f"{format_ugx(biggest.loan_principal)} principal, {format_ugx(biggest.loan_due)} due, "
            f"net {format_ugx(biggest.net)}.",
            "Make sure the surety and the repayment date are written, not remembered.",
            cards=[biggest],
            score=0.4, tags=["biggest-loan"],
        ))
    risky = [c for c in cards if c.risk_label in {"critical", "high"}]
    if risky:
        out.append(_pattern(
            "member.risk_list", "member_health", "high",
            f"{len(risky)} member(s) sit in high or critical risk.",
            "; ".join(f"{c.name} ({c.risk_label}, score {c.risk_score})" for c in risky[:6]) + ".",
            "Review this shortlist first when you open the next meeting.",
            cards=risky,
            score=0.77, tags=["scorecard"],
        ))
    return out


# ---------------------------------------------------------------------------
# Group health score
# ---------------------------------------------------------------------------

def score_group(patterns: list[Pattern], snapshot: dict[str, Any]) -> tuple[int, str]:
    t = snapshot.get("totals") or {}
    score = 78
    if t.get("member_count", 0) == 0:
        return 20, "fragile"

    # Count each family once at its worst severity so a single messy
    # borrower does not subtract sixteen overlapping flags.
    worst: dict[str, str] = {}
    goods = 0
    for p in patterns:
        if p.severity == "good":
            goods += 1
            continue
        prev = worst.get(p.family)
        if prev is None or SEVERITY_ORDER.get(p.severity, 0) > SEVERITY_ORDER.get(prev, 0):
            worst[p.family] = p.severity
    for sev in worst.values():
        if sev == "critical":
            score -= 12
        elif sev == "high":
            score -= 7
        elif sev == "watch":
            score -= 3
    score += min(9, goods * 2)

    exposure = float(t.get("loan_to_savings_ratio") or 0)
    if exposure >= 1:
        score -= 10
    elif exposure >= 0.7:
        score -= 5

    inactive = int(t.get("inactive_count") or 0)
    members = max(1, int(t.get("member_count") or 1))
    if inactive / members >= 0.5:
        score -= 6

    if ugx(t.get("balance_vs_principal")) < 0:
        score -= 10
    elif ugx(t.get("balance_vs_principal")) > 0 and exposure < 0.7:
        # Messy attendance is not an empty cash box.
        score = max(score, 42)

    score = max(18, min(96, score))
    return score, _health_label(score)


def pick_recommendations(patterns: list[Pattern], snapshot: dict[str, Any]) -> list[str]:
    ranked = sorted(
        patterns,
        key=lambda p: (SEVERITY_ORDER.get(p.severity, 0), p.score),
        reverse=True,
    )
    recs: list[str] = []
    seen = set()
    for p in ranked:
        if p.severity in {"good", "info"} and len(recs) >= 2:
            continue
        action = p.action.strip()
        if not action or action in seen:
            continue
        seen.add(action)
        recs.append(action)
        if len(recs) >= 5:
            break
    if not recs:
        recs.append("Keep recording every deposit, loan, fine and repayment on the day it happens.")
    return recs


def pick_follow_ups(patterns: list[Pattern], cards: list[MemberScorecard], snapshot: dict[str, Any]) -> list[str]:
    questions = []
    if any(p.id.startswith("credit.overexposed") for p in patterns):
        questions.append("Who is most over-borrowed right now?")
    if any("quiet" in p.id or "inactive" in p.tags for p in patterns):
        questions.append("Who has not deposited this week?")
    if any(p.family == "growth" for p in patterns):
        questions.append("How is the savings trend this month?")
    if any(p.family == "liquidity" for p in patterns):
        questions.append("What is the group balance after loans?")
    if cards:
        questions.append(f"How is {cards[0].name} doing?")
    questions.append("Give me a short health report.")
    # unique
    out = []
    seen = set()
    for q in questions:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out[:5]


def speak_brief(score: int, label: str, snapshot: dict[str, Any], patterns: list[Pattern]) -> tuple[str, str, str]:
    t = snapshot["totals"]
    brief = (
        f"The group looks {label} (health {score}/100). "
        f"Savings {format_ugx(t.get('savings'))}, "
        f"loans due {format_ugx(t.get('loans_with_interest'))}, "
        f"cash after principal {format_ugx(t.get('balance_vs_principal'))}, "
        f"profit {format_ugx(t.get('profit'))}."
    )
    risks = [p for p in patterns if p.severity in {"critical", "high"}]
    goods = [p for p in patterns if p.severity == "good"]
    if risks:
        risk_line = "Main concerns: " + " ".join(p.headline for p in risks[:3])
    else:
        risk_line = "No critical or high-severity flags on the book right now."
    if goods:
        good_line = "Holding up well: " + " ".join(p.headline for p in goods[:3])
    else:
        good_line = "No standout healthy patterns yet — the books may still be thin."
    return brief, risk_line, good_line


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_patterns(
    snapshot: dict[str, Any] | None = None,
    db_path: str = "SAVIO-database/SAVIO.db",
) -> PatternBrief:
    snap = snapshot if snapshot is not None else load_snapshot(db_path)
    cards = build_scorecards(snap)
    patterns: list[Pattern] = []
    patterns.extend(detect_liquidity(snap))
    patterns.extend(detect_credit_risk(snap, cards))
    patterns.extend(detect_participation(snap, cards))
    patterns.extend(detect_concentration(snap, cards))
    patterns.extend(detect_discipline(snap, cards))
    patterns.extend(detect_growth(snap))
    patterns.extend(detect_profit(snap))
    patterns.extend(detect_governance(snap, cards))
    patterns.extend(detect_combined(snap, cards))
    patterns.extend(detect_member_highlights(cards))

    patterns.sort(key=lambda p: (SEVERITY_ORDER.get(p.severity, 0), p.score), reverse=True)
    score, label = score_group(patterns, snap)
    brief, risks, goods = speak_brief(score, label, snap, patterns)
    one_line = f"{label.title()} group — health {score}/100. {patterns[0].headline}" if patterns else f"{label.title()} group — health {score}/100."

    ratios = {
        "loan_to_savings": snap["totals"].get("loan_to_savings_ratio"),
        "exposure_label": snap["totals"].get("exposure_label"),
        "inactive_rate": ratio(snap["totals"].get("inactive_count"), snap["totals"].get("member_count")),
        "overexposed_rate": ratio(snap["totals"].get("overexposed_count"), snap["totals"].get("member_count")),
        "profit": ugx(snap["totals"].get("profit")),
        "coverage_vs_due": ratio(snap["totals"].get("savings"), snap["totals"].get("loans_with_interest")),
        "savings_hhi": _herfindahl([c.savings_share for c in cards]),
        "loan_hhi": _herfindahl([c.loan_share for c in cards if c.loan_principal > 0]),
        "savings_gini": _gini([c.savings for c in cards]),
    }

    return PatternBrief(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        group_health_score=score,
        group_health_label=label,
        one_line_brief=one_line,
        patterns=patterns,
        scorecards=cards,
        ratios=ratios,
        recommendations=pick_recommendations(patterns, snap),
        follow_ups=pick_follow_ups(patterns, cards, snap),
        spoken_brief=brief,
        spoken_risks=risks,
        spoken_goods=goods,
    )


def patterns_for_member(brief: PatternBrief, member_id: int) -> list[Pattern]:
    return [p for p in brief.patterns if member_id in p.member_ids]


def scorecard_for_member(brief: PatternBrief, member_id: int) -> MemberScorecard | None:
    for card in brief.scorecards:
        if card.id == member_id:
            return card
    return None


def format_patterns_for_prompt(brief: PatternBrief, limit: int = 12) -> str:
    """Compact block injected into Gemini so it reasons from named patterns."""
    lines = [
        "PATTERN ENGINE (authoritative — treat these as already-computed signals):",
        f"- Generated: {brief.generated_at}",
        f"- Group health: {brief.group_health_label} ({brief.group_health_score}/100)",
        f"- One-line: {brief.one_line_brief}",
        f"- Brief: {brief.spoken_brief}",
        f"- Risks: {brief.spoken_risks}",
        f"- Strengths: {brief.spoken_goods}",
        "- Key ratios: "
        + ", ".join(f"{k}={v}" for k, v in brief.ratios.items()),
        "- Top patterns:",
    ]
    for p in brief.patterns[:limit]:
        who = f" | members: {', '.join(p.member_names[:4])}" if p.member_names else ""
        lines.append(
            f"  * [{p.severity}/{p.family}] {p.id}: {p.headline} "
            f"{p.detail}{who} | action: {p.action}"
        )
    if brief.recommendations:
        lines.append("- Recommended next steps:")
        for rec in brief.recommendations:
            lines.append(f"  * {rec}")
    if brief.scorecards:
        lines.append("- Member risk scorecards (highest risk first):")
        for c in brief.scorecards[:8]:
            lines.append(
                f"  * #{c.id} {c.name}: {c.risk_label} {c.risk_score}/100, "
                f"savings {format_ugx(c.savings)}, loan {format_ugx(c.loan_principal)}, "
                f"net {format_ugx(c.net)}, inactive_7d={c.inactive_7d}, "
                f"overexposed={c.overexposed}"
            )
            if c.concerns:
                lines.append(f"    concerns: {'; '.join(c.concerns)}")
            if c.strengths:
                lines.append(f"    strengths: {'; '.join(c.strengths)}")
    lines.append("- Do not invent extra patterns. If you mention a risk, quote these figures.")
    lines.append("- Prefer combined patterns over listing raw rows.")
    return "\n".join(lines)


def format_brief_for_user(brief: PatternBrief) -> str:
    """A ready-to-show professional brief when the model is offline."""
    chunks = [brief.spoken_brief, brief.spoken_risks, brief.spoken_goods]
    if brief.recommendations:
        chunks.append("What to do next:")
        for i, rec in enumerate(brief.recommendations, 1):
            chunks.append(f"{i}. {rec}")
    return " ".join(chunks[:3]) + "\n" + "\n".join(chunks[3:])


# Intent helpers used by the Brain / ask pipeline
PATTERN_INTENT_HINTS = {
    "group_health": ["spoken_brief", "spoken_risks", "spoken_goods"],
    "overexposed": ["credit.overexposed", "combo.quiet_overexposed"],
    "inactive_members": ["part.all_quiet", "part.many_quiet", "part.some_quiet"],
    "loan_exposure": ["credit.group_exposure_high", "credit.group_exposure_moderate", "credit.group_exposure_low"],
    "savings_trend": ["growth.month_down", "growth.month_up", "growth.month_steady", "growth.no_recent"],
    "top_savers": ["member.top_saver", "conc.savings_topheavy"],
    "biggest_loan": ["member.biggest_loan", "conc.loans_topheavy"],
    "repeat_fines": ["disc.heavy_fines", "disc.some_fines", "combo.fined_overexposed"],
    "total_profit": ["profit.mix", "disc.profit_from_fines"],
    "group_balance": ["liq.negative_principal", "liq.interest_uncovered", "liq.thin_buffer", "liq.healthy"],
}


def patterns_for_intent(brief: PatternBrief, intent: str) -> list[Pattern]:
    wanted = set(PATTERN_INTENT_HINTS.get(intent, []))
    if not wanted:
        return brief.patterns[:6]
    picked = [p for p in brief.patterns if p.id in wanted or p.family in wanted]
    return picked or brief.patterns[:4]


def spoken_for_intent(brief: PatternBrief, intent: str) -> str:
    if intent == "group_health":
        rec = " Next: " + brief.recommendations[0] if brief.recommendations else ""
        return f"{brief.spoken_brief} {brief.spoken_risks}{rec}"
    picked = patterns_for_intent(brief, intent)
    if not picked:
        return brief.spoken_brief
    return " ".join(p.spoken() for p in picked[:3])
