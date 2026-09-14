"""
Quick sanity check for the Brain + Pattern engine.

Creates a throwaway SQLite file, loads a small chama, and prints
how the assistant reads messy questions. Run from this folder:

    python3 SAVIO_selftest.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from Python_Brain import load_snapshot, understand_question
from SAVIO_patterns import analyze_patterns, format_brief_for_user
from SAVIO_voice import compose_draft, merge_follow_up, recall, remember, TurnMemory


def _build_db(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firstname TEXT, surname TEXT, number TEXT, surety TEXT,
            deposite TEXT, status TEXT
        );
        CREATE TABLE deposit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER, amount INTEGER, type INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE loan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER, amount INTEGER, interest INTEGER,
            created_at TEXT
        );
        CREATE TABLE welfare (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER, amount INTEGER, created_at TEXT
        );
        CREATE TABLE fine (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER, amount INTEGER, reason TEXT, created_at TEXT
        );
        CREATE TABLE payment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER, payment INTEGER, created_at TEXT
        );
        """
    )
    members = [
        ("John", "Okello", "0700000001", "Mary Achieng", 180000, "active"),
        ("Mary", "Achieng", "0700000002", "John Okello", 420000, "active"),
        ("Peter", "Mugisha", "0700000003", "", 25000, "active"),
        ("Aisha", "Nalwoga", "0700000004", "Mary Achieng", 90000, "active"),
        ("Samuel", "Tusiime", "", "John Okello", 0, "active"),
    ]
    for row in members:
        cur.execute(
            "INSERT INTO members (firstname, surname, number, surety, deposite, status) VALUES (?,?,?,?,?,?)",
            row,
        )
    # Deposits: Mary is the anchor, Peter is tiny, Samuel never saved,
    # John saved but not this week, Aisha saved this week.
    now = "datetime('now','localtime')"
    week_ago = "datetime('now','localtime','-8 days')"
    two_weeks = "datetime('now','localtime','-15 days')"
    deposits = [
        (1, 80000, week_ago),
        (1, 100000, two_weeks),
        (2, 200000, "datetime('now','localtime','-1 days')"),
        (2, 220000, week_ago),
        (3, 25000, two_weeks),
        (4, 40000, "datetime('now','localtime','-2 days')"),
        (4, 50000, two_weeks),
    ]
    for member_id, amount, when_sql in deposits:
        cur.execute(
            f"INSERT INTO deposit (member_id, amount, created_at) VALUES (?, ?, {when_sql})",
            (member_id, amount),
        )
    # Peter borrowed far more than he saved. John has a moderate loan and is quiet.
    cur.execute(
        f"INSERT INTO loan (member_id, amount, interest, created_at) VALUES (3, 120000, 10, {two_weeks})"
    )
    cur.execute(
        f"INSERT INTO loan (member_id, amount, interest, created_at) VALUES (1, 90000, 8, {week_ago})"
    )
    cur.execute(
        f"INSERT INTO fine (member_id, amount, reason, created_at) VALUES (3, 15000, 'late meeting', {week_ago})"
    )
    cur.execute(
        f"INSERT INTO welfare (member_id, amount, created_at) VALUES (2, 10000, {now})"
    )
    con.commit()
    con.close()


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "SAVIO-database"
    tmp.mkdir()
    db = str(tmp / "SAVIO.db")
    _build_db(db)

    snap = load_snapshot(db)
    brief = analyze_patterns(snap)
    print("=== GROUP BRIEF ===")
    print(format_brief_for_user(brief))
    print()
    print("Health:", brief.group_health_label, brief.group_health_score)
    print("Patterns:")
    for p in brief.patterns[:10]:
        print(f"  [{p.severity}] {p.id}: {p.headline}")

    questions = [
        "how mch hs the grp saved",
        "who nt deposited",
        "how is peter",
        "totl loan",
        "who is at risk",
        "what should we do",
        "how is the group",
        "if peter pays 20000 what is left",
        "top savrs",
        "how much does mary owe",
    ]
    print("\n=== MESSY QUESTIONS ===")
    last = None
    for q in questions:
        u = understand_question(q, db_path=db, snapshot=snap)
        u = __import__("Python_Brain", fromlist=["attach_patterns"]).attach_patterns(u, brief)
        intent = (u.facts or {}).get("resolved_intent") or (u.intents[0]["intent"] if u.intents else "-")
        print(f"Q: {q}")
        print(f"  intent={intent} members={[m['name'] for m in u.members]}")
        print(f"  {u.exact_answer}")
        draft = compose_draft(
            exact_answer=u.exact_answer,
            intent=intent,
            patterns=[p.as_dict() for p in brief.patterns[:4]],
            recommendations=brief.recommendations,
            member_name=u.members[0]["name"] if u.members else None,
        )
        print(f"  draft[{draft['tone']}]: {draft['draft'][:160]}")
        last = u
        print()

    remember("t", TurnMemory(
        question="how is peter",
        intent="member_status",
        member_ids=[3],
        member_names=["Peter Mugisha"],
        exact_answer="Peter snapshot",
    ))
    expanded = merge_follow_up("and loans", recall("t"))
    print("Follow-up 'and loans' expanded to:", expanded)


if __name__ == "__main__":
    main()
