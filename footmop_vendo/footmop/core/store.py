"""SQLite persistence: stock, sessions/transactions, coin log, faults.

Durability: WAL journal + synchronous=FULL, and every state change that
matters (each coin, each dispensed item, each stock change) is its own
committed transaction, so a power cut loses at most the event in flight.

Threading: one Store instance is used only from the GUI thread.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("footmop.store")

SCHEMA_VERSION = 1

# Session status values
OPEN = "OPEN"                    # customer choosing / paying
DISPENSING = "DISPENSING"        # paid, motor sequence running
COMPLETED = "COMPLETED"
PARTIAL_FAULT = "PARTIAL_FAULT"  # dispense failed part-way; items owed
CANCELLED = "CANCELLED"          # timed out / cancelled with no credit
ABANDONED = "ABANDONED"          # timed out with credit (forfeited, logged)
INTERRUPTED = "INTERRUPTED"      # power lost mid-transaction (found at boot)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    status      TEXT NOT NULL,
    qty         INTEGER NOT NULL DEFAULT 0,
    unit_price  INTEGER NOT NULL DEFAULT 0,
    amount_due  INTEGER NOT NULL DEFAULT 0,
    credit      INTEGER NOT NULL DEFAULT 0,
    dispensed   INTEGER NOT NULL DEFAULT 0,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
CREATE TABLE IF NOT EXISTS coins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    session_id  INTEGER REFERENCES sessions(id),
    value       INTEGER NOT NULL,
    pulses      INTEGER NOT NULL,
    context     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coins_session ON coins(session_id);
CREATE TABLE IF NOT EXISTS stock_moves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    delta       INTEGER NOT NULL,
    stock_after INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    session_id  INTEGER REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS faults (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    code        TEXT NOT NULL,
    message     TEXT NOT NULL,
    session_id  INTEGER,
    owed_items  INTEGER NOT NULL DEFAULT 0,
    cleared_at  TEXT
);
"""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str, initial_price: int):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # manual transactions
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)
        with self._tx():
            self._setdefault("schema_version", str(SCHEMA_VERSION))
            self._setdefault("stock", "0")
            self._setdefault("price", str(initial_price))
        integrity = self.db.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            log.error("database integrity check: %s", integrity)

    # -------------------------------------------------------------- plumbing
    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def _setdefault(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))

    def _get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass

    # ----------------------------------------------------------------- stock
    def get_stock(self) -> int:
        return int(self._get("stock", "0"))

    def _move_stock(self, delta: int, reason: str, session_id: Optional[int]) -> int:
        new = max(0, self.get_stock() + delta)
        self._set("stock", str(new))
        self.db.execute(
            "INSERT INTO stock_moves(ts, delta, stock_after, reason, session_id) VALUES (?,?,?,?,?)",
            (_now(), delta, new, reason, session_id))
        return new

    def set_stock(self, value: int, reason: str = "admin_set") -> int:
        value = max(0, int(value))
        with self._tx():
            return self._move_stock(value - self.get_stock(), reason, None)

    # ----------------------------------------------------------------- price
    def get_price(self) -> int:
        return int(self._get("price", "0"))

    def set_price(self, price: int) -> None:
        with self._tx():
            self._set("price", str(int(price)))

    # -------------------------------------------------------------- sessions
    def open_session(self, unit_price: int) -> int:
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO sessions(started_at, status, unit_price) VALUES (?,?,?)",
                (_now(), OPEN, unit_price))
            return int(cur.lastrowid)

    def add_coin(self, session_id: Optional[int], value: int, pulses: int, context: str) -> None:
        """Log a coin; if attached to a session, also add to its credit (atomic)."""
        with self._tx():
            self.db.execute(
                "INSERT INTO coins(ts, session_id, value, pulses, context) VALUES (?,?,?,?,?)",
                (_now(), session_id, value, pulses, context))
            if session_id is not None:
                self.db.execute("UPDATE sessions SET credit = credit + ? WHERE id=?",
                                (value, session_id))

    def set_order(self, session_id: int, qty: int, unit_price: int, amount_due: int) -> None:
        """Record what the customer chose, so abandoned orders show it too."""
        with self._tx():
            self.db.execute("UPDATE sessions SET qty=?, unit_price=?, amount_due=? WHERE id=?",
                            (qty, unit_price, amount_due, session_id))

    def begin_dispense(self, session_id: int, qty: int, unit_price: int, amount_due: int) -> None:
        with self._tx():
            self.db.execute(
                "UPDATE sessions SET status=?, qty=?, unit_price=?, amount_due=? WHERE id=?",
                (DISPENSING, qty, unit_price, amount_due, session_id))

    def record_item_dispensed(self, session_id: Optional[int], reason: str = "sale") -> int:
        """One item confirmed dropped: stock -1 and session.dispensed +1, atomically."""
        with self._tx():
            if session_id is not None:
                self.db.execute("UPDATE sessions SET dispensed = dispensed + 1 WHERE id=?",
                                (session_id,))
            return self._move_stock(-1, reason, session_id)

    def end_session(self, session_id: int, status: str, note: Optional[str] = None) -> None:
        with self._tx():
            self.db.execute("UPDATE sessions SET status=?, ended_at=?, note=COALESCE(?, note) "
                            "WHERE id=?", (status, _now(), note, session_id))

    def get_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    # ---------------------------------------------------------------- faults
    def add_fault(self, code: str, message: str, session_id: Optional[int] = None,
                  owed_items: int = 0) -> int:
        with self._tx():
            cur = self.db.execute(
                "INSERT INTO faults(ts, code, message, session_id, owed_items) VALUES (?,?,?,?,?)",
                (_now(), code, message, session_id, owed_items))
            return int(cur.lastrowid)

    def active_fault(self) -> Optional[Dict[str, Any]]:
        row = self.db.execute(
            "SELECT * FROM faults WHERE cleared_at IS NULL ORDER BY id LIMIT 1").fetchone()
        return dict(row) if row else None

    def active_faults(self) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM faults WHERE cleared_at IS NULL ORDER BY id")]

    def clear_faults(self) -> int:
        with self._tx():
            cur = self.db.execute("UPDATE faults SET cleared_at=? WHERE cleared_at IS NULL",
                                  (_now(),))
            return cur.rowcount

    # -------------------------------------------------------------- recovery
    def recover_on_boot(self) -> List[str]:
        """Close out anything a power cut left half-done. Returns findings."""
        findings: List[str] = []
        rows = self.db.execute(
            "SELECT * FROM sessions WHERE status IN (?, ?)", (OPEN, DISPENSING)).fetchall()
        for r in rows:
            sid = r["id"]
            if r["status"] == DISPENSING:
                owed = max(0, r["qty"] - r["dispensed"])
                msg = (f"Power was lost while dispensing order #{sid}: "
                       f"{r['dispensed']} of {r['qty']} confirmed. {owed} item(s) owed.")
                self.end_session(sid, INTERRUPTED, msg)
                if owed:
                    self.add_fault("power_interrupted", msg, sid, owed)
                findings.append(msg)
            elif r["credit"] > 0:
                msg = f"Power lost during payment for order #{sid}; P{r['credit']} credit logged as abandoned."
                self.end_session(sid, ABANDONED, msg)
                findings.append(msg)
            else:
                self.end_session(sid, CANCELLED, "closed at boot (no credit)")
        for f in findings:
            log.warning("recovery: %s", f)
        return findings

    # ---------------------------------------------------------------- reports
    def sales_summary(self) -> Dict[str, Any]:
        today = _dt.date.today().isoformat()

        def agg(where: str, args: tuple) -> Dict[str, int]:
            row = self.db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(dispensed),0) items, COALESCE(SUM(credit),0) cash "
                f"FROM sessions WHERE {where}", args).fetchone()
            return {"orders": row["n"], "items": row["items"], "cash": row["cash"]}

        sold = f"status IN ('{COMPLETED}','{PARTIAL_FAULT}','{INTERRUPTED}')"
        abandoned = self.db.execute(
            "SELECT COALESCE(SUM(credit),0) FROM sessions WHERE status=?", (ABANDONED,)).fetchone()[0]
        unclaimed = self.db.execute(
            "SELECT COALESCE(SUM(value),0) FROM coins WHERE session_id IS NULL "
            "AND context != 'admin_test'").fetchone()[0]
        owed = self.db.execute(
            "SELECT COALESCE(SUM(qty - dispensed),0) FROM sessions WHERE status IN (?,?)",
            (PARTIAL_FAULT, INTERRUPTED)).fetchone()[0]
        return {
            "today": agg(f"{sold} AND started_at >= ?", (today,)),
            "all_time": agg(sold, ()),
            "abandoned_credit": abandoned,
            "unclaimed_coins": unclaimed,
            "owed_items": owed,
        }

    def recent_sessions(self, n: int = 5) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM sessions WHERE status != ? ORDER BY id DESC LIMIT ?", (CANCELLED, n))]

    def export_json(self) -> str:
        return json.dumps({"summary": self.sales_summary(),
                           "recent": self.recent_sessions(50)}, indent=2)
