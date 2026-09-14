#IMPORT SECTION
import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Allow this file to import the Brain / pattern modules from the same folder
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from google import genai
    from google.genai import types as genai_types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    from Python_Brain import (
        attach_patterns,
        format_understanding_for_prompt,
        format_ugx,
        load_snapshot,
        local_reply,
        understand_question,
        ugx,
    )
    BRAIN_AVAILABLE = True
except ImportError:
    BRAIN_AVAILABLE = False

try:
    from SAVIO_patterns import (
        analyze_patterns,
        format_brief_for_user,
        format_patterns_for_prompt,
        scorecard_for_member,
    )
    PATTERNS_AVAILABLE = True
except ImportError:
    PATTERNS_AVAILABLE = False

try:
    from SAVIO_voice import (
        SYSTEM_GEMINI_CORE,
        SYSTEM_SAVIO_CORE,
        TurnMemory,
        classify_meta,
        compose_draft,
        format_voice_for_prompt,
        glossary_answer,
        greeting_answer,
        help_answer,
        merge_follow_up,
        recall,
        remember,
        thanks_answer,
    )
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False

try:
    from SAVIO_playbook import (
        format_loan_help,
        format_meeting_help,
        format_playbook_for_prompt,
        format_shareout_help,
    )
    PLAYBOOK_AVAILABLE = True
except ImportError:
    PLAYBOOK_AVAILABLE = False

# SAVIO AI CONFIG
# Gemini has a genuinely free API tier - grab a key at https://aistudio.google.com/apikey
# then set it as an environment variable before starting the server, e.g.
#   export GEMINI_API_KEY="AIza..."       (Mac/Linux)
#   setx GEMINI_API_KEY "AIza..."         (Windows)
SAVIO_AI_MODEL = "gemini-3.6-flash"
_gemini_client = None


def get_ai_client():
    """Lazily create the Gemini client so the server still starts without a key."""
    global _gemini_client
    if not GEMINI_AVAILABLE:
        return None
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=api_key)
    return _gemini_client

#AUTO DATABASE CREATION
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set")


def get_connection():
    return psycopg2.connect(DATABASE_URL)

SAVIO = FastAPI()

#CREATING A LINK
SAVIO.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the database/table
connection = sqlite3.connect("SAVIO-database/SAVIO.db")
cursor = connection.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firstname TEXT NOT NULL,
    surname TEXT NOT NULL,
    number TEXT NOT NULL,
    surety TEXT NOT NULL,
    deposite TEXT NOT NULL,
    status TEXT NOT NULL
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS deposit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    type INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS loan(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    interest INTEGER NOT NULL
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS welfare(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    amount INTEGER NOT NULL
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS fine(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS payment(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id INTEGER NOT NULL,
    payment INTEGER NOT NULL
)
""")

connection.commit()
connection.close()

def ensure_column(cursor, table, column, definition):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


connection = sqlite3.connect("SAVIO-database/SAVIO.db")
cursor = connection.cursor()
# SQLite won't allow a non-constant default (like datetime('now')) on ADD COLUMN,
# so the column is added bare - every INSERT below already sets created_at explicitly.
ensure_column(cursor, "deposit", "created_at", "TEXT")
ensure_column(cursor, "loan", "created_at", "TEXT")
ensure_column(cursor, "welfare", "created_at", "TEXT")
ensure_column(cursor, "fine", "created_at", "TEXT")
ensure_column(cursor, "payment", "created_at", "TEXT")

# Backfill any pre-existing rows that predate this column (fresh installs have none)
for _table in ("deposit", "loan", "welfare", "fine", "payment"):
    cursor.execute(
        f"UPDATE {_table} SET created_at = datetime('now','localtime') WHERE created_at IS NULL"
    )
connection.commit()
connection.close()

#MAKING AN EXCEPTED INPUTS
class Member(BaseModel):
    firstname: str
    surname: str
    number: str
    surety: str
    deposite: int
    status: str

class Deposit(BaseModel):
    amount: int

class Loan(BaseModel):
    amount: int
    interest: int

class Welfare(BaseModel):
    amount: int

class Fine(BaseModel):
    amount: int
    reason: str = ""

class Payment(BaseModel):
    amount: int

class AskMessage(BaseModel):
    role: str
    content: str

class AskRequest(BaseModel):
    question: str
    history: list[AskMessage] = []
    mode: str = "SAVIO"  # "SAVIO" (grounded in group data) or "gemini" (general assistant)
    session_id: str = "default"


def get_connection():
    return psycopg2.connect(DATABASE_URL)


#COMMUNICATION DECK
@SAVIO.post("/member")
def adding_member(member: Member):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
    INSERT INTO members (firstname, surname, number, surety, deposite, status)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id
""", (
    member.firstname,
    member.surname,
    member.number,
    member.surety,
    member.deposite,
    member.status
))

member_id = cursor.fetchone()[0]

    member_id = cursor.lastrowid

    # Keep group totals in sync by recording the opening deposit
    if member.deposite:
        cursor.execute("""
            INSERT INTO deposit (member_id, amount, created_at)
            VALUES (?, ?, datetime('now','localtime'))
        """, (
            member_id,
            member.deposite
        ))

    connection.commit()
    connection.close()

    return {"message": "member saved"}


@SAVIO.post("/account/{member_id}/deposit")
def deposit(member_id: int, deposit: Deposit):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
    INSERT INTO deposit (member_id, amount, created_at)
    VALUES (%s, %s, CURRENT_TIMESTAMP)
""", (
    member_id,
    member.deposite
))
    cursor.execute("""
        UPDATE members SET deposite = deposite + ? WHERE id = ?
    """, (
        deposit.amount,
        member_id
    ))

    connection.commit()
    connection.close()

    return {"message": "deposit saved"}


@SAVIO.post("/account/{member_id}/loan")
def loan(member_id: int, loan: Loan):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT * FROM loan WHERE member_id = ?",
        (member_id,)
    )
    current_loan = cursor.fetchone()

    if current_loan:
        new_amount = current_loan[2] + loan.amount
        cursor.execute(
            """
            UPDATE loan
            SET amount = ?, interest = ?
            WHERE member_id = ?
            """,
            (
                new_amount,
                loan.interest,
                member_id
            )
        )
    else:
        cursor.execute(
            """
            INSERT INTO loan (member_id, amount, interest, created_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
            """,
            (
                member_id,
                loan.amount,
                loan.interest
            )
        )

    connection.commit()
    connection.close()

    return {"message": "Loan updated"}

@SAVIO.post("/account/{member_id}/welfare")
def add_welfare(member_id: int, welfare: Welfare):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id FROM members WHERE id = ?",
        (member_id,)
    )
    member = cursor.fetchone()
    if not member:
        connection.close()
        return {"message": "member not found"}

    cursor.execute("""
        INSERT INTO welfare (member_id, amount, created_at)
        VALUES (?, ?, datetime('now','localtime'))
    """, (
        member_id,
        welfare.amount
    ))

    connection.commit()
    connection.close()

    return {"message": "welfare saved"}

@SAVIO.post("/account/{member_id}/fine")
def add_fine(member_id: int, fine: Fine):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        "SELECT id FROM members WHERE id = ?",
        (member_id,)
    )
    member = cursor.fetchone()
    if not member:
        connection.close()
        return {"message": "member not found"}

    cursor.execute("""
        INSERT INTO fine (member_id, amount, reason, created_at)
        VALUES (?, ?, ?, datetime('now','localtime'))
    """, (
        member_id,
        fine.amount,
        fine.reason
    ))

    connection.commit()
    connection.close()

    return {"message": "fine saved"}

@SAVIO.post("/account/{member_id}/payment")
def make_payment(member_id: int, payment: Payment):
    connection = get_connection()
    cursor = connection.cursor()

    if payment.amount <= 0:
        connection.close()
        return {"message": "payment amount must be greater than 0"}

    cursor.execute(
        "SELECT id FROM members WHERE id = ?",
        (member_id,)
    )
    member = cursor.fetchone()
    if not member:
        connection.close()
        return {"message": "member not found"}

    cursor.execute(
        "SELECT id, amount FROM loan WHERE member_id = ?",
        (member_id,)
    )
    current_loan = cursor.fetchone()

    if not current_loan:
        connection.close()
        return {"message": "this member has no loan to pay"}

    remaining = current_loan[1]
    if remaining <= 0:
        connection.close()
        return {"message": "this loan is already cleared"}

    applied = min(payment.amount, remaining)

    cursor.execute("""
        INSERT INTO payment(member_id, payment, created_at)
        VALUES(?, ?, datetime('now','localtime'))
    """, (
        member_id,
        applied
    ))

    cursor.execute("""
        UPDATE loan
        SET amount = amount - ?
        WHERE member_id = ?
    """, (
        applied,
        member_id
    ))

    connection.commit()
    connection.close()

    if applied < payment.amount:
        return {"message": f"loan cleared. applied {applied} of {payment.amount}"}

    return {"message": "payment saved"}

@SAVIO.get("/members")
def retrieve_members():
    picker = get_connection()
    cursor = picker.cursor()
    cursor.execute("SELECT * FROM members")
    members = cursor.fetchall()
    picker.close()
    return members


@SAVIO.get("/account/{member_id}")
def account(member_id: int):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM members WHERE id = ?",
        (member_id,)
    )
    member = cursor.fetchone()
    connection.close()
    return {"member": member}


@SAVIO.get("/account/{member_id}/loan")
def get_loan(member_id: int):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM loan WHERE member_id = ?",
        (member_id,)
    )
    loan = cursor.fetchone()
    connection.close()
    return {"loan": loan}


@SAVIO.get("/account/{member_id}/welfare")
def get_welfare(member_id: int):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM welfare WHERE member_id = ?",
        (member_id,)
    )
    total = cursor.fetchone()[0]
    connection.close()
    return {"welfare": total}


@SAVIO.get("/account/{member_id}/fine")
def get_fine(member_id: int):
    connection = get_connection()
    cursor = connection.cursor()
    cursor.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM fine WHERE member_id = ?",
        (member_id,)
    )
    total = cursor.fetchone()[0]
    connection.close()
    return {"fine": total}


@SAVIO.get("/account/{member_id}/payment")
def get_payment(member_id: int):
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(payment), 0)
        FROM payment
        WHERE member_id = ?
    """, (member_id,))

    total = cursor.fetchone()[0]

    connection.close()

    return {"payment": total}



@SAVIO.get("/group/status")
def group_status():
  connection = get_connection()
  cursor = connection.cursor()

  cursor.execute("SELECT COALESCE(SUM(amount),0) FROM deposit")

  total_saving = cursor.fetchone()[0]
  connection.close()

  return{"total_savings":total_saving}

@SAVIO.get("/group/loan-status")
def loan_status():
  connection = get_connection()
  cursor = connection.cursor()
  cursor.execute("SELECT COALESCE(SUM(amount + (amount * interest/100.0)), 0) FROM loan")
  total_loan = cursor.fetchone()[0]
  connection.close()

  return{"total_loans":total_loan}

@SAVIO.get("/group/balance-status")
def balance_status():
  connection = get_connection()
  cursor = connection.cursor()

  cursor.execute("""
      SELECT
          (SELECT COALESCE(SUM(amount), 0) FROM deposit)
          -
          (SELECT COALESCE(SUM(amount), 0) FROM loan)
  """)

  balance = cursor.fetchone()[0]

  connection.close()

  return {"balance": balance}

@SAVIO.get("/group/welfare-status")
def welfare_status():
  connection = get_connection()
  cursor = connection.cursor()

  cursor.execute("SELECT COALESCE(SUM(amount),0) FROM welfare")
  total_welfare=cursor.fetchone()[0]
  connection.close()

  return{"total_welfare":total_welfare}

@SAVIO.get("/group/fine-status")
def fine_status():
  connection = get_connection()
  cursor = connection.cursor()

  cursor.execute("SELECT COALESCE(SUM(amount),0) FROM fine")

  total_fine = cursor.fetchone()[0]
  connection.close()
  return{"total_fines":total_fine}

@SAVIO.get("/group/profits-status")
def profits_status():
  connection = get_connection()
  cursor = connection.cursor()

  cursor.execute("""
    SELECT(
    SELECT COALESCE(SUM(interest),0) FROM loan
    ) + (
    SELECT COALESCE(SUM(amount),0) FROM fine
    )
  """)

  total_profit= cursor.fetchone()[0]
  connection.close()
  return{"total_profit":total_profit}


@SAVIO.get("/records")
def get_records():
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        SELECT
            'savings-' || d.id AS record_id,
            d.member_id,
            TRIM(m.firstname || ' ' || m.surname) AS name,
            'savings' AS type,
            d.amount,
            COALESCE(d.created_at, datetime('now','localtime')) AS created_at
        FROM deposit d
        JOIN members m ON m.id = d.member_id

        UNION ALL

        SELECT
            'loan-' || l.id,
            l.member_id,
            TRIM(m.firstname || ' ' || m.surname),
            'loan',
            l.amount,
            COALESCE(l.created_at, datetime('now','localtime'))
        FROM loan l
        JOIN members m ON m.id = l.member_id

        UNION ALL

        SELECT
            'interest-' || l.id,
            l.member_id,
            TRIM(m.firstname || ' ' || m.surname),
            'interest',
            CAST(ROUND(l.amount * l.interest / 100.0) AS INTEGER),
            COALESCE(l.created_at, datetime('now','localtime'))
        FROM loan l
        JOIN members m ON m.id = l.member_id
        WHERE COALESCE(l.interest, 0) > 0

        UNION ALL

        SELECT
            'welfare-' || w.id,
            w.member_id,
            TRIM(m.firstname || ' ' || m.surname),
            'welfare',
            w.amount,
            COALESCE(w.created_at, datetime('now','localtime'))
        FROM welfare w
        JOIN members m ON m.id = w.member_id

        UNION ALL

        SELECT
            'fine-' || f.id,
            f.member_id,
            TRIM(m.firstname || ' ' || m.surname),
            'fine',
            f.amount,
            COALESCE(f.created_at, datetime('now','localtime'))
        FROM fine f
        JOIN members m ON m.id = f.member_id

        ORDER BY created_at DESC, record_id DESC
    """)

    rows = cursor.fetchall()
    connection.close()

    records = []
    for row in rows:
        records.append({
            "id": row[0],
            "member_id": row[1],
            "name": row[2],
            "type": row[3],
            "amount": row[4],
            "created_at": row[5]
        })

    return records


#SAVIO AI SECTION
def build_group_context():
    """Pulls together a compact snapshot of the group's finances for SAVIO AI to reason over."""
    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("SELECT COALESCE(SUM(amount),0) FROM deposit")
    total_savings = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount + (amount * interest/100.0)),0) FROM loan")
    total_loans = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount),0) FROM welfare")
    total_welfare = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(amount),0) FROM fine")
    total_fines = cursor.fetchone()[0]

    cursor.execute("""
        SELECT (SELECT COALESCE(SUM(interest),0) FROM loan)
             + (SELECT COALESCE(SUM(amount),0) FROM fine)
    """)
    total_profit = cursor.fetchone()[0]

    balance = total_savings - total_loans

    cursor.execute("SELECT id, firstname, surname, deposite, status FROM members")
    members = cursor.fetchall()

    cursor.execute("""
        SELECT m.id, m.firstname, m.surname, l.amount, l.interest
        FROM loan l JOIN members m ON m.id = l.member_id
        WHERE l.amount > 0
    """)
    active_loans = cursor.fetchall()

    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
        SELECT m.id, m.firstname, m.surname FROM members m
        WHERE m.id NOT IN (
            SELECT member_id FROM deposit WHERE created_at >= ?
        )
    """, (seven_days_ago,))
    inactive_members = cursor.fetchall()

    # ---- Derived signals: trends, ratios, and risk flags for the AI to reason with,
    # instead of leaving it to eyeball raw rows and recompute things itself. ----

    # 4-week savings trend (oldest -> newest) so the AI can spot direction, not just a total.
    now = datetime.now()
    weekly_trend = []
    for i in range(3, -1, -1):
        week_start = (now - timedelta(days=7 * (i + 1))).strftime("%Y-%m-%d %H:%M:%S")
        week_end = (now - timedelta(days=7 * i)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ? AND created_at < ?",
            (week_start, week_end)
        )
        weekly_trend.append(cursor.fetchone()[0])

    # Group-level loan exposure relative to savings - a single ratio the AI can qualify
    # ("healthy" / "stretched" / "risky") instead of comparing two raw totals itself.
    loan_to_savings_ratio = (total_loans / total_savings) if total_savings > 0 else 0
    if total_loans == 0:
        exposure_label = "no outstanding loans"
    elif loan_to_savings_ratio < 0.3:
        exposure_label = "conservative - loans are a small share of savings"
    elif loan_to_savings_ratio < 0.7:
        exposure_label = "moderate - keep an eye on it"
    else:
        exposure_label = "high - loans are close to or exceed available savings"

    # Members whose current loan already exceeds their own savings - a default-risk pattern,
    # not visible from either list alone.
    cursor.execute("""
        SELECT m.id, m.firstname, m.surname, m.deposite, l.amount
        FROM loan l JOIN members m ON m.id = l.member_id
        WHERE l.amount > COALESCE(m.deposite, 0)
    """)
    overexposed_members = cursor.fetchall()

    # Members with 2+ fines - a repeat-behavior pattern rather than isolated incidents.
    cursor.execute("""
        SELECT m.id, m.firstname, m.surname, COUNT(*) as fine_count, COALESCE(SUM(f.amount),0) as fine_total
        FROM fine f JOIN members m ON m.id = f.member_id
        GROUP BY f.member_id
        HAVING COUNT(*) >= 2
        ORDER BY fine_count DESC
    """)
    repeat_fine_members = cursor.fetchall()

    # Top 3 savers - a positive pattern worth being able to point to, same as the risk ones.
    cursor.execute("""
        SELECT id, firstname, surname, deposite FROM members
        WHERE deposite > 0
        ORDER BY deposite DESC LIMIT 3
    """)
    top_savers = cursor.fetchall()

    connection.close()

    member_lines = [
        f"- #{m[0]} {m[1]} {m[2]} | savings: UGX {int(m[3] or 0):,} | status: {m[4]}"
        for m in members
    ]
    loan_lines = [
        f"- #{l[0]} {l[1]} {l[2]} owes UGX {int(l[3] or 0):,} at {int(l[4] or 0)}% interest"
        for l in active_loans
    ]
    inactive_lines = [f"- #{m[0]} {m[1]} {m[2]}" for m in inactive_members]

    week_labels = ["4 weeks ago", "3 weeks ago", "2 weeks ago", "this past week"]
    trend_lines = [
        f"- {label}: UGX {int(amount):,}"
        for label, amount in zip(week_labels, weekly_trend)
    ]
    if weekly_trend[-1] > weekly_trend[0]:
        trend_direction = "rising over the last month"
    elif weekly_trend[-1] < weekly_trend[0]:
        trend_direction = "falling over the last month"
    else:
        trend_direction = "flat over the last month"

    overexposed_lines = [
        f"- #{m[0]} {m[1]} {m[2]}: owes UGX {int(m[4] or 0):,} against UGX {int(m[3] or 0):,} saved"
        for m in overexposed_members
    ]
    repeat_fine_lines = [
        f"- #{m[0]} {m[1]} {m[2]}: fined {m[3]} times, UGX {int(m[4] or 0):,} total"
        for m in repeat_fine_members
    ]
    top_saver_lines = [
        f"- #{m[0]} {m[1]} {m[2]}: UGX {int(m[3] or 0):,}"
        for m in top_savers
    ]

    context = f"""SAVINGS GROUP SNAPSHOT
Total savings: UGX {total_savings:,}
Total outstanding loans (incl. interest): UGX {total_loans:,}
Available balance: UGX {balance:,}
Total welfare collected: UGX {total_welfare:,}
Total fines collected: UGX {total_fines:,}
Total interest + fine profit: UGX {total_profit:,}
Member count: {len(members)}

MEMBERS:
{chr(10).join(member_lines) if member_lines else "No members yet."}

ACTIVE LOANS:
{chr(10).join(loan_lines) if loan_lines else "No active loans."}

MEMBERS WITH NO DEPOSIT IN THE LAST 7 DAYS:
{chr(10).join(inactive_lines) if inactive_lines else "None - everyone has deposited recently."}

PATTERNS & SIGNALS (derived - reason with these, don't just restate them):
- Savings trend, oldest to newest week ({trend_direction}):
{chr(10).join(trend_lines)}
- Group loan exposure: UGX {total_loans:,} against UGX {total_savings:,} saved (ratio {loan_to_savings_ratio:.2f}) - {exposure_label}
- Members whose current loan already exceeds their own savings (default-risk pattern):
{chr(10).join(overexposed_lines) if overexposed_lines else "None - no member is borrowed beyond their savings."}
- Members fined 2 or more times (repeat-behavior pattern, not just isolated incidents):
{chr(10).join(repeat_fine_lines) if repeat_fine_lines else "None - no repeat offenders."}
- Top savers (positive pattern worth recognizing):
{chr(10).join(top_saver_lines) if top_saver_lines else "No savings recorded yet."}
"""
    return context, {
        "total_savings": total_savings,
        "total_loans": total_loans,
        "balance": balance,
        "total_welfare": total_welfare,
        "total_fines": total_fines,
        "total_profit": total_profit,
        "member_count": len(members),
        "inactive_count": len(inactive_members),
        "loan_to_savings_ratio": round(loan_to_savings_ratio, 2),
        "overexposed_count": len(overexposed_members),
        "repeat_fine_count": len(repeat_fine_members),
    }


@SAVIO.get("/ai/overview")
def ai_overview():
    _, stats = build_group_context()
    return stats


@SAVIO.get("/ai/recent-activity")
def ai_recent_activity(limit: int = 5):
    records = get_records()
    return records[:limit]


@SAVIO.get("/ai/insight")
def ai_insight():
    connection = get_connection()
    cursor = connection.cursor()

    now = datetime.now()
    this_week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    last_week_start = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ?",
        (this_week_start,)
    )
    this_week = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COALESCE(SUM(amount),0) FROM deposit WHERE created_at >= ? AND created_at < ?",
        (last_week_start, this_week_start)
    )
    last_week = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*) FROM members m
        WHERE m.id NOT IN (
            SELECT member_id FROM deposit WHERE created_at >= ?
        )
    """, (this_week_start,))
    inactive_count = cursor.fetchone()[0]

    connection.close()

    change = this_week - last_week
    if change > 0:
        trend = f"The group's savings increased by UGX {change:,} this week."
    elif change < 0:
        trend = f"The group's savings dropped by UGX {abs(change):,} this week."
    else:
        trend = "The group's savings stayed flat this week."

    inactive_line = (
        f"{inactive_count} member{'s' if inactive_count != 1 else ''} have not made a deposit in the last 7 days."
        if inactive_count else "Every member has made a deposit in the last 7 days."
    )

    return {"insight": f"{trend} {inactive_line}"}


@SAVIO.post("/ai/report")
def ai_report():
    client = get_ai_client()
    if client is None:
        return {
            "error": True,
            "message": "SAVIO AI isn't set up yet. Set the GEMINI_API_KEY environment variable on the server and restart it."
        }

    context, _ = build_group_context()
    _, brief = _load_live_brain()
    extra = format_patterns_for_prompt(brief) if brief is not None and PATTERNS_AVAILABLE else ""
    play = ""
    if brief is not None and PLAYBOOK_AVAILABLE:
        play = format_playbook_for_prompt([p.id for p in brief.patterns[:8]])

    response = client.models.generate_content(
        model=SAVIO_AI_MODEL,
        contents=context + "\n\n" + extra + "\n\n" + play,
        config=genai_types.GenerateContentConfig(
            system_instruction=(
                "You are SAVIO AI, the financial assistant built into a savings-group (chama/VSLA) "
                "management app. Write a short, clear financial report for the group's leadership "
                "based on the PATTERN ENGINE and computed figures. Cover: overall health score, "
                "savings trend, loan risk, combined patterns (quiet + over-borrowed), and two "
                "concrete next steps from the playbook. Use plain language, short paragraphs or "
                "bullet points, and always refer to amounts in UGX. Keep it under 220 words. "
                "Do not invent members or figures."
            ),
            max_output_tokens=4000,
            thinking_config=genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.LOW),
        )
    )

    if not response.text:
        return {"error": True, "message": "SAVIO AI didn't return a report this time - try Regenerate."}

    return {"error": False, "report": response.text}


if VOICE_AVAILABLE:
    SAVIO_SYSTEM_INSTRUCTION = SYSTEM_SAVIO_CORE
    GEMINI_SYSTEM_INSTRUCTION = SYSTEM_GEMINI_CORE
else:
    SAVIO_SYSTEM_INSTRUCTION = (
        "You are SAVIO AI. Use only the group data and computed figures given to you. "
        "Never invent members or amounts. Keep answers short. Always use UGX."
    )
    GEMINI_SYSTEM_INSTRUCTION = (
        "You are Gemini inside SAVIO. You may explain savings-group ideas. "
        "Do not invent this group's figures."
    )


def _load_live_brain(db_path: str = "SAVIO-database/SAVIO.db"):
    snap = None
    brief = None
    if BRAIN_AVAILABLE:
        try:
            snap = load_snapshot(db_path)
        except Exception:
            snap = None
    if PATTERNS_AVAILABLE and snap is not None:
        try:
            brief = analyze_patterns(snap)
        except Exception:
            brief = None
    return snap, brief


@SAVIO.get("/ai/patterns")
def ai_patterns():
    """Named pattern brief — what the assistant actually reasons from."""
    if not PATTERNS_AVAILABLE:
        return {"error": True, "message": "Pattern engine is not loaded."}
    snap, brief = _load_live_brain()
    if brief is None:
        return {"error": True, "message": "Could not build a pattern brief from the database."}
    return {"error": False, "brief": brief.as_dict()}


@SAVIO.get("/ai/scorecards")
def ai_scorecards():
    if not PATTERNS_AVAILABLE:
        return {"error": True, "message": "Pattern engine is not loaded."}
    _, brief = _load_live_brain()
    if brief is None:
        return {"error": True, "message": "Could not score members."}
    return {"error": False, "scorecards": [c.as_dict() for c in brief.scorecards]}


@SAVIO.get("/ai/brief")
def ai_brief():
    if not PATTERNS_AVAILABLE:
        return {"error": True, "message": "Pattern engine is not loaded."}
    _, brief = _load_live_brain()
    if brief is None:
        return {"error": True, "message": "Could not build a brief."}
    return {
        "error": False,
        "health": brief.group_health_label,
        "score": brief.group_health_score,
        "brief": brief.spoken_brief,
        "risks": brief.spoken_risks,
        "strengths": brief.spoken_goods,
        "recommendations": brief.recommendations,
        "follow_ups": brief.follow_ups,
        "text": format_brief_for_user(brief),
    }


def _meta_answer(question: str, snap: dict | None) -> str | None:
    if not VOICE_AVAILABLE:
        return None
    kind = classify_meta(question)
    if kind == "greeting":
        totals = (snap or {}).get("totals") if snap else None
        return greeting_answer(totals)
    if kind == "thanks":
        return thanks_answer()
    if kind == "help":
        return help_answer()
    if kind == "glossary":
        return glossary_answer(question)
    if kind == "empty":
        return help_answer()
    return None


def _compose_system_prompt(mode: str, u, brief, context: str) -> str:
    base = GEMINI_SYSTEM_INSTRUCTION if mode == "gemini" else SAVIO_SYSTEM_INSTRUCTION
    parts = [base]
    if BRAIN_AVAILABLE and u is not None:
        parts.append(format_understanding_for_prompt(u))
    if PATTERNS_AVAILABLE and brief is not None:
        parts.append(format_patterns_for_prompt(brief))
        if PLAYBOOK_AVAILABLE:
            pattern_ids = [p.id for p in brief.patterns[:8]]
            if u and isinstance(getattr(u, "facts", None), dict):
                pattern_ids = list(u.facts.get("pattern_ids") or pattern_ids)
            parts.append(format_playbook_for_prompt(pattern_ids))
    if VOICE_AVAILABLE and u is not None:
        if brief is not None:
            try:
                from SAVIO_patterns import patterns_for_intent as _pfi
                intent_for_voice = u.intents[0]["intent"] if u.intents else "group_health"
                # Prefer the remapped compute intent when facts carry pattern ids
                if u.facts.get("pattern_ids"):
                    intent_for_voice = intent
                related = _pfi(brief, intent_for_voice)
                pattern_dicts = [p.as_dict() for p in (related or brief.patterns[:4])]
            except Exception:
                pattern_dicts = [p.as_dict() for p in brief.patterns[:4]]
        else:
            pattern_dicts = []
        recs = brief.recommendations if brief else []
        member_name = u.members[0]["name"] if u.members else None
        intent = u.intents[0]["intent"] if u.intents else "group_health"
        draft = compose_draft(
            exact_answer=u.exact_answer,
            intent=intent,
            patterns=pattern_dicts,
            recommendations=recs,
            member_name=member_name,
        )
        parts.append(format_voice_for_prompt(draft))
        if PLAYBOOK_AVAILABLE and intent == "meeting_help":
            parts.append("MEETING HELP:\n" + format_meeting_help())
        if PLAYBOOK_AVAILABLE and intent == "shareout_help":
            parts.append("SHARE-OUT HELP:\n" + format_shareout_help())
        if PLAYBOOK_AVAILABLE and intent == "loan_policy":
            parts.append("LOAN HELP:\n" + format_loan_help())
    parts.append("CURRENT GROUP DATA (backup context, prefer the layers above):\n" + context)
    return "\n\n".join(parts)


@SAVIO.post("/ai/ask")
def ai_ask(request: AskRequest):
    mode = request.mode if request.mode in ("SAVIO", "gemini") else "SAVIO"
    question = (request.question or "").strip()
    session_id = request.session_id or "default"

    snap, brief = _load_live_brain()
    context, _stats = build_group_context()

    if VOICE_AVAILABLE:
        previous = recall(session_id)
        question = merge_follow_up(question, previous)

    meta = _meta_answer(question, snap)
    if meta and mode == "SAVIO":
        return {
            "error": False,
            "mode": mode,
            "answer": meta,
            "grounded": True,
            "intent": classify_meta(question) if VOICE_AVAILABLE else "meta",
        }

    understanding = None
    if BRAIN_AVAILABLE:
        try:
            understanding = understand_question(question, snapshot=snap)
            if PATTERNS_AVAILABLE:
                understanding = attach_patterns(understanding, brief)
        except Exception:
            understanding = None

    if VOICE_AVAILABLE and understanding is not None:
        remember(session_id, TurnMemory(
            question=question,
            intent=understanding.intents[0]["intent"] if understanding.intents else "",
            member_ids=[m["id"] for m in understanding.members],
            member_names=[m["name"] for m in understanding.members],
            amounts=list(understanding.amounts),
            exact_answer=understanding.exact_answer,
        ))

    client = get_ai_client()
    if client is None:
        # Stay useful even without a Gemini key — the brain already computed the answer.
        if understanding and understanding.exact_answer:
            answer = local_reply(understanding)
            if brief and understanding.intents and understanding.intents[0]["intent"] == "group_health":
                answer = format_brief_for_user(brief)
            return {
                "error": False,
                "mode": mode,
                "answer": answer,
                "grounded": True,
                "offline": True,
                "understanding": understanding.as_dict() if understanding else None,
            }
        return {
            "error": True,
            "mode": mode,
            "answer": "SAVIO AI isn't set up yet. Set GEMINI_API_KEY, or ask a direct figure question so the Python brain can answer offline."
        }

    system_instruction = _compose_system_prompt(mode, understanding, brief, context)

    contents = [
        {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
        for m in request.history
    ]
    contents.append({"role": "user", "parts": [{"text": question}]})

    response = client.models.generate_content(
        model=SAVIO_AI_MODEL,
        contents=contents,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=3000,
            thinking_config=genai_types.ThinkingConfig(thinking_level=genai_types.ThinkingLevel.LOW),
        )
    )

    if not response.text:
        fallback = local_reply(understanding) if understanding else "SAVIO AI didn't return an answer that time — try asking again."
        return {
            "error": True,
            "mode": mode,
            "answer": fallback,
        }

    return {
        "error": False,
        "mode": mode,
        "answer": response.text,
        "grounded": True,
        "intent": (understanding.intents[0]["intent"] if understanding and understanding.intents else None),
        "members": (
            [{"id": m["id"], "name": m["name"]} for m in understanding.members]
            if understanding else []
        ),
    }
