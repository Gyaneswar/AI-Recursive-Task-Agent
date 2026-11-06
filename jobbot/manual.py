from datetime import datetime

from .models import Job


def add_manual(path: str, job: Job, score: int | None, reason: str):
    line = (f"{datetime.now():%Y-%m-%d %H:%M} | score={score if score is not None else '-'} | "
            f"{job.company} | {job.title} | {job.location} | {job.apply_url or job.url} | {reason}\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
