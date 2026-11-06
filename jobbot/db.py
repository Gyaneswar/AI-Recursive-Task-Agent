import re
import sqlite3
from datetime import datetime, date, timedelta

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, company TEXT, title TEXT, url TEXT,
    score INTEGER, status TEXT, reason TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS boards (
    ats TEXT, slug TEXT, origin TEXT, status TEXT DEFAULT 'active', added_at TEXT, polled_at TEXT,
    PRIMARY KEY (ats, slug)
);
CREATE TABLE IF NOT EXISTS probes (company TEXT PRIMARY KEY COLLATE NOCASE, found TEXT, probed_at TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


APPLIED = "('submitted', 'needs_manual')"


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def seen(self, job_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM jobs WHERE id=? AND status NOT IN ('dry_run', 'matched', 'error')", (job_id,)
        ).fetchone() is not None

    def applied_same_role(self, job: Job) -> bool:
        rows = self.conn.execute(
            f"SELECT title FROM jobs WHERE lower(company)=lower(?) AND id<>? AND status IN {APPLIED}",
            (job.company, job.id),
        ).fetchall()
        return any(_norm(t) == _norm(job.title) for (t,) in rows)

    def company_count(self, company: str) -> int:
        return self.conn.execute(
            f"SELECT COUNT(*) FROM jobs WHERE lower(company)=lower(?) AND status IN {APPLIED}", (company,)
        ).fetchone()[0]

    def upsert(self, job: Job, status: str, score: int | None = None, reason: str = ""):
        self.conn.execute(
            """INSERT INTO jobs (id, source, company, title, url, score, status, reason, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET score=COALESCE(excluded.score, score),
               status=excluded.status, reason=excluded.reason, updated_at=excluded.updated_at""",
            (job.id, job.source, job.company, job.title, job.url, score, status, reason,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def count_today(self, source: str | None = None, status: str = "submitted") -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE (? IS NULL OR source=?) AND status=? AND updated_at LIKE ?",
            (source, source, status, f"{date.today().isoformat()}%"),
        ).fetchone()[0]

    def add_board(self, ats: str, slug: str, origin: str) -> bool:
        cur = self.conn.execute("INSERT OR IGNORE INTO boards (ats, slug, origin, added_at) VALUES (?,?,?,?)",
                                (ats, slug, origin, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        return cur.rowcount > 0

    def due_boards(self, poll_hours: float) -> list[tuple[str, str]]:
        cutoff = (datetime.now() - timedelta(hours=poll_hours)).isoformat(timespec="seconds")
        return self.conn.execute(
            "SELECT ats, slug FROM boards WHERE status='active' AND (polled_at IS NULL OR polled_at < ?) "
            "ORDER BY polled_at IS NOT NULL, polled_at, rowid", (cutoff,)).fetchall()

    def mark_board(self, ats: str, slug: str, status: str = "active"):
        self.conn.execute("UPDATE boards SET status=?, polled_at=? WHERE ats=? AND slug=?",
                          (status, datetime.now().isoformat(timespec="seconds"), ats, slug))
        self.conn.commit()

    def unprobed_companies(self, limit: int, extra: list[str] = ()) -> list[str]:
        names = list(dict.fromkeys(c.strip() for c in extra if c and c.strip()))
        names += [c for (c,) in self.conn.execute(
            "SELECT MAX(company) FROM jobs WHERE company <> '' GROUP BY lower(company) "
            "ORDER BY MAX(updated_at) DESC").fetchall()]
        done = {c.lower() for (c,) in self.conn.execute("SELECT company FROM probes").fetchall()}
        out = []
        for c in names:
            if c.lower() not in done and c.lower() not in {x.lower() for x in out}:
                out.append(c)
        return out[:limit]

    def add_probe(self, company: str, found: list[tuple[str, str]]):
        self.conn.execute("INSERT OR REPLACE INTO probes (company, found, probed_at) VALUES (?,?,?)",
                          (company, ",".join(f"{a}:{s}" for a, s in found),
                           datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()
