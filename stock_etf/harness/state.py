from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    """Small SQLite-backed store for run metadata and stage transitions.

    This store intentionally contains run metadata, not API secrets or raw
    brokerage credentials. Create one instance per database path as needed.
    """

    def __init__(self, path: str | Path = "backend_state/harness_runs.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS stages (
                    run_id TEXT NOT NULL,
                    stage_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    started_at TEXT,
                    finished_at TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (run_id, stage_name, attempt),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def create_run(self, run_id: str, config: dict[str, Any]) -> None:
        now = _now()
        with self._connect() as db:
            db.execute(
                """INSERT INTO runs
                   (run_id, status, config_json, metadata_json, created_at, updated_at)
                   VALUES (?, 'running', ?, '{}', ?, ?)""",
                (run_id, json.dumps(config, default=str), now, now),
            )

    def set_run_status(self, run_id: str, status: str,
                       metadata: dict[str, Any] | None = None) -> None:
        allowed = {"running", "completed", "failed", "cancelled"}
        if status not in allowed:
            raise ValueError(f"Unsupported run status: {status}")
        with self._connect() as db:
            cur = db.execute(
                """UPDATE runs SET status=?, metadata_json=?, updated_at=?
                   WHERE run_id=?""",
                (status, json.dumps(metadata or {}, default=str), _now(), run_id),
            )
            if cur.rowcount != 1:
                raise KeyError(f"Unknown run_id: {run_id}")

    def start_stage(self, run_id: str, stage_name: str,
                    attempt: int = 1, details: dict[str, Any] | None = None) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO stages
                   (run_id, stage_name, status, attempt, started_at, details_json)
                   VALUES (?, ?, 'running', ?, ?, ?)""",
                (run_id, stage_name, attempt, _now(),
                 json.dumps(details or {}, default=str)),
            )

    def finish_stage(self, run_id: str, stage_name: str, status: str,
                     attempt: int = 1, details: dict[str, Any] | None = None) -> None:
        if status not in {"completed", "failed", "skipped"}:
            raise ValueError(f"Unsupported stage status: {status}")
        with self._connect() as db:
            cur = db.execute(
                """UPDATE stages SET status=?, finished_at=?, details_json=?
                   WHERE run_id=? AND stage_name=? AND attempt=?""",
                (status, _now(), json.dumps(details or {}, default=str),
                 run_id, stage_name, attempt),
            )
            if cur.rowcount != 1:
                raise KeyError(f"Unknown stage: {run_id}/{stage_name}/{attempt}")

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                return None
            stages = db.execute(
                "SELECT * FROM stages WHERE run_id=? ORDER BY started_at, stage_name",
                (run_id,),
            ).fetchall()
        result = dict(run)
        result["config"] = json.loads(result.pop("config_json"))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        result["stages"] = []
        for stage in stages:
            item = dict(stage)
            item["details"] = json.loads(item.pop("details_json"))
            result["stages"].append(item)
        return result
