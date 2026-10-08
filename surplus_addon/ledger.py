"""Own research ledger; never opens or changes the operational database."""

import json
import math
import sqlite3
from decimal import Decimal
from pathlib import Path
from threading import RLock


class Ledger:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        path.chmod(0o600)
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS decisions(
                id INTEGER PRIMARY KEY, at TEXT NOT NULL, evidence TEXT NOT NULL, body TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cycles(
                id TEXT PRIMARY KEY, session TEXT NOT NULL, account TEXT NOT NULL,
                evidence TEXT NOT NULL, con_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
                loss TEXT NOT NULL, fees TEXT NOT NULL, turnover TEXT NOT NULL,
                seconds REAL NOT NULL, body TEXT NOT NULL
            );
        """)

    def record(self, at, evidence, decision):
        with self.lock:
            self.connection.execute(
                "INSERT INTO decisions(at,evidence,body) VALUES(?,?,?)",
                (at.isoformat(), evidence, json.dumps(decision, allow_nan=False)),
            )
            self.connection.commit()

    def cycle(self, cycle):
        if cycle["evidence"] != "synthetic":
            raise ValueError("v1 only records simulated cycles; no paper-observed calibration exists")
        execution_expense = Decimal(str(cycle.get("execution_expense", cycle["fees"])))
        if (
            any(not Decimal(str(cycle[k])).is_finite() for k in ("loss", "fees", "turnover"))
            or Decimal(str(cycle["fees"])) < 0
            or Decimal(str(cycle["turnover"])) <= 0
            or not math.isfinite(cycle["seconds"])
            or cycle["seconds"] <= 0
            or cycle["quantity"] <= 0
            or not execution_expense.is_finite()
            or execution_expense < Decimal(str(cycle["fees"]))
        ):
            raise ValueError("Finite, valid synthetic cycle accounting required")
        with self.lock:
            return self._insert_cycle(cycle)

    def _insert_cycle(self, cycle):
        result = self.connection.execute(
            "INSERT OR IGNORE INTO cycles VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                cycle["id"],
                cycle["session"],
                cycle["account"],
                cycle["evidence"],
                cycle["con_id"],
                cycle["quantity"],
                str(cycle["loss"]),
                str(cycle["fees"]),
                str(cycle["turnover"]),
                cycle["seconds"],
                json.dumps(cycle, allow_nan=False),
            ),
        )
        self.connection.commit()
        return result.rowcount == 1

    def stats(self, session, account, evidence):
        with self.lock:
            rows = self.connection.execute(
                "SELECT loss,fees,turnover,seconds,body FROM cycles WHERE session=? AND account=? AND evidence=?",
                (str(session), account, evidence),
            ).fetchall()
        losses = [Decimal(r[0]) for r in rows]
        fees = sum((Decimal(r[1]) for r in rows), Decimal(0))
        seconds = sum(r[3] for r in rows)
        return {
            "cycles": len(rows),
            "orders": 2 * len(rows),
            "confirmed_loss": float(sum(losses, Decimal(0))),
            "fees": float(fees),
            "expense_used": float(
                sum(
                    (
                        max(
                            Decimal(0),
                            Decimal(r[0]),
                            Decimal(r[1]),
                            Decimal(str(json.loads(r[4]).get("execution_expense", r[1]))),
                        )
                        for r in rows
                    ),
                    Decimal(0),
                )
            ),
            "turnover": float(sum((Decimal(r[2]) for r in rows), Decimal(0))),
            "observed_loss_per_second": float(sum(losses, Decimal(0))) / seconds if seconds else None,
            "evidence": evidence,
            "paper_calibrated": False,
        }

    def history(self):
        with self.lock:
            return [
                json.loads(r[0])
                for r in self.connection.execute("SELECT body FROM decisions ORDER BY id DESC LIMIT 30")
            ]

    def cycles(self):
        with self.lock:
            return [
                json.loads(r[0])
                for r in self.connection.execute("SELECT body FROM cycles ORDER BY rowid DESC LIMIT 30")
            ]

    def close(self):
        with self.lock:
            self.connection.close()
