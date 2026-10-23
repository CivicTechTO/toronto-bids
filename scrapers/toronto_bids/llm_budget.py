"""Local Claude spending guard, independent of the public procurement database.

Reserve a conservative request allowance atomically before sending. Failed/aborted
requests keep that allowance unless the server definitively rejected them. The
Console workspace limit remains the authority for actual account billing.
"""

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


class BudgetExceeded(ValueError):
    """No more requests may be sent until the budget or month changes."""


def microdollars(value: str) -> int:
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("TB_LLM_MONTHLY_USD must be a dollar amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("TB_LLM_MONTHLY_USD must be a finite nonnegative amount")
    return int(amount * 1_000_000)


class UsageBudget:
    def __init__(self, path: Path, monthly_usd: str):
        self.path = path
        self.limit = microdollars(monthly_usd)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("""CREATE TABLE IF NOT EXISTS request (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, month TEXT NOT NULL,
            model TEXT NOT NULL, charged_microdollars INTEGER NOT NULL,
            status TEXT NOT NULL, usage_json TEXT
        )""")
        return conn

    def reserve(self, model: str, allowance: int, *, conn=None) -> int:
        if conn is None:
            own = self._connect()
            try:
                own.execute("BEGIN IMMEDIATE")
                result = self.reserve(model, allowance, conn=own)
                own.commit()
                return result
            finally:
                own.close()
        now = datetime.now(timezone.utc).isoformat()
        # Outstanding reservations carry across month boundaries until billing
        # is known, so an overnight batch cannot evade the next month's guard.
        used = conn.execute(
            "SELECT COALESCE(SUM(charged_microdollars), 0) FROM request "
            "WHERE month=? OR status='reserved'", (now[:7],),
        ).fetchone()[0]
        if used + allowance > self.limit:
            raise BudgetExceeded(
                f"Claude monthly budget would be exceeded: "
                f"${used / 1_000_000:.4f} charged/reserved, "
                f"${allowance / 1_000_000:.4f} needed; "
                f"limit ${self.limit / 1_000_000:.2f}. "
                "See TB_LLM_MONTHLY_USD and llm-usage.sqlite."
            )
        cursor = conn.execute(
            "INSERT INTO request (created_at, month, model, charged_microdollars, status) "
            "VALUES (?, ?, ?, ?, 'reserved')", (now, now[:7], model, allowance),
        )
        return cursor.lastrowid

    def settle(self, request_id: int, charge: int, status: str, usage=None, *, conn=None):
        if conn is None:
            own = self._connect()
            try:
                with own:
                    self.settle(request_id, charge, status, usage, conn=own)
            finally:
                own.close()
            return
        conn.execute(
            "UPDATE request SET charged_microdollars=?, status=?, usage_json=?, month=? WHERE id=?",
            (charge, status, json.dumps(usage) if usage is not None else None,
             datetime.now(timezone.utc).strftime("%Y-%m"), request_id),
        )
