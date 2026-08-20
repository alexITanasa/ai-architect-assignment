"""SQLite persistence layer for usage logs."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_logs (
    request_id       TEXT PRIMARY KEY,
    model            TEXT NOT NULL,
    model_group      TEXT,
    provider         TEXT,
    user_id          TEXT,
    call_type        TEXT,
    prompt_tokens    INTEGER,
    completion_tokens INTEGER,
    reasoning_tokens INTEGER,
    total_tokens     INTEGER,
    spend            REAL,
    input_cost       REAL,
    output_cost      REAL,
    reasoning_cost   REAL,
    duration_ms      INTEGER,
    start_time       TEXT,
    end_time         TEXT,
    ingested_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_start_time  ON usage_logs(start_time);
CREATE INDEX IF NOT EXISTS idx_model_group ON usage_logs(model_group);
CREATE INDEX IF NOT EXISTS idx_user_id     ON usage_logs(user_id);
"""


def init_db(db_path: str) -> None:
    """Create the DB file and schema if they don't exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # dict-like rows
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_log(conn: sqlite3.Connection, log: dict) -> bool:
    """Insert a single log. Returns True if inserted, False if duplicate."""
    metadata = log.get("metadata") or {}
    usage_obj = metadata.get("usage_object") or {}
    completion_details = usage_obj.get("completion_tokens_details") or {}
    cost_breakdown = metadata.get("cost_breakdown") or {}

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO usage_logs (
            request_id, model, model_group, provider, user_id, call_type,
            prompt_tokens, completion_tokens, reasoning_tokens, total_tokens,
            spend, input_cost, output_cost, reasoning_cost,
            duration_ms, start_time, end_time
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            log.get("request_id"),
            log.get("model"),
            log.get("model_group"),
            log.get("custom_llm_provider"),
            log.get("user"),
            log.get("call_type"),
            log.get("prompt_tokens"),
            log.get("completion_tokens"),
            completion_details.get("reasoning_tokens"),
            log.get("total_tokens"),
            log.get("spend"),
            cost_breakdown.get("input_cost"),
            cost_breakdown.get("output_cost"),
            cost_breakdown.get("reasoning_cost"),
            log.get("request_duration_ms"),
            log.get("startTime"),
            log.get("endTime"),
        ),
    )
    return cur.rowcount > 0


def get_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregated metrics for the dashboard."""
    kpis = conn.execute(
        """
        SELECT
            COUNT(*)                          AS total_requests,
            COALESCE(SUM(spend), 0)           AS total_spend,
            COALESCE(SUM(total_tokens), 0)    AS total_tokens,
            COALESCE(AVG(duration_ms), 0)     AS avg_duration_ms
        FROM usage_logs
        """
    ).fetchone()

    by_model = conn.execute(
        """
        SELECT
            model_group,
            COUNT(*)                       AS requests,
            COALESCE(SUM(spend), 0)        AS spend,
            COALESCE(SUM(total_tokens), 0) AS tokens
        FROM usage_logs
        GROUP BY model_group
        ORDER BY spend DESC
        """
    ).fetchall()

    by_user = conn.execute(
        """
        SELECT
            user_id,
            COUNT(*)                       AS requests,
            COALESCE(SUM(spend), 0)        AS spend
        FROM usage_logs
        GROUP BY user_id
        ORDER BY spend DESC
        LIMIT 10
        """
    ).fetchall()

    since = (datetime.utcnow() - timedelta(days=7)).isoformat()
    by_day = conn.execute(
        """
        SELECT
            SUBSTR(start_time, 1, 10) AS day,
            COUNT(*)                  AS requests,
            COALESCE(SUM(spend), 0)   AS spend
        FROM usage_logs
        WHERE start_time >= ?
        GROUP BY day
        ORDER BY day
        """,
        (since,),
    ).fetchall()

    return {
        "kpis": dict(kpis),
        "by_model": [dict(r) for r in by_model],
        "by_user": [dict(r) for r in by_user],
        "by_day": [dict(r) for r in by_day],
    }


def get_logs(
    conn: sqlite3.Connection, limit: int = 100, model_group: str | None = None
) -> list[dict]:
    """Recent logs, optionally filtered by model_group."""
    if model_group:
        rows = conn.execute(
            "SELECT * FROM usage_logs WHERE model_group = ? "
            "ORDER BY start_time DESC LIMIT ?",
            (model_group, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM usage_logs ORDER BY start_time DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_latest_start_time(conn: sqlite3.Connection) -> str | None:
    """Newest start_time — used as cursor for incremental fetching."""
    row = conn.execute(
        "SELECT MAX(start_time) AS ts FROM usage_logs"
    ).fetchone()
    return row["ts"]
