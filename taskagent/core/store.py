import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT, fingerprint TEXT, started_at TEXT, finished_at TEXT, status TEXT, summary TEXT, trace TEXT
);
CREATE INDEX IF NOT EXISTS task_runs_task ON task_runs (task, id);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, response TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def start_run(self, task: str, fingerprint: str) -> int:
        cur = self.conn.execute("INSERT INTO task_runs (task, fingerprint, started_at, status) VALUES (?,?,?,?)",
                                (task, fingerprint, now(), "running"))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, status: str, summary: str = "", trace: str = ""):
        self.conn.execute("UPDATE task_runs SET finished_at=?, status=?, summary=?, trace=? WHERE id=?",
                          (now(), status, summary, trace, run_id))
        self.conn.commit()

    def last_run(self, task: str) -> tuple[str, str, str] | None:
        return self.conn.execute(
            "SELECT started_at, fingerprint, status FROM task_runs WHERE task=? ORDER BY id DESC LIMIT 1", (task,)
        ).fetchone()

    def last_result(self, task: str, fingerprint: str) -> str:
        row = self.conn.execute(
            "SELECT summary FROM task_runs WHERE task=? AND fingerprint=? AND status IN ('done', 'partial', 'stopped') "
            "AND summary <> '' ORDER BY id DESC LIMIT 1", (task, fingerprint)).fetchone()
        return row[0] if row else ""

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def cache_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def cache_put(self, key: str, response: str):
        self.conn.execute("INSERT OR REPLACE INTO llm_cache (key, response, created_at) VALUES (?,?,?)",
                          (key, response, now()))
        self.conn.commit()
