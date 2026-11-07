import json
import re
from dataclasses import replace

from taskagent.core.llm import LLM

from .models import Job, MatchResult

MATCH_SYSTEM = """You are a strict technical recruiter. Compare the candidate profile with the job.
Score 0-100 for how well the candidate fits (skills, seniority, domain, location).
Also classify the job from its text:
- remote_scope: "worldwide" if fully remote and open to hiring from any country, "restricted" if remote but
  limited to specific countries/regions, "onsite" if onsite or hybrid, "unknown" if unclear.
- pay_currency: ISO code of the stated salary/compensation currency (e.g. "USD", "INR", "EUR"), or "unknown"
  if no pay is stated. Ignore money that is not pay (funding, revenue, valuation).
Be honest; do not inflate. Output JSON:
{"score": int, "reasons": [str], "missing": [str], "remote_scope": str, "pay_currency": str}"""


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def passes_filters(job: Job, filters: dict) -> tuple[bool, str]:
    title = _norm(job.title)
    if any(x in title for x in filters.get("title_exclude", [])):
        return False, "title excluded"
    include = filters.get("title_include", [])
    if include and not any(x in title for x in include):
        return False, "title not in include list"
    locs = filters.get("locations", [])
    if locs and job.location:
        loc = _norm(job.location)
        has = lambda terms: any(re.search(rf"\b{re.escape(t)}\b", loc) for t in terms)
        remote_ok = "remote" in locs and has(["remote"]) and not has(filters.get("remote_exclude", []))
        if not (has([x for x in locs if x != "remote"]) or remote_ok):
            return False, f"location '{job.location}' not allowed"
    return True, ""


def passes_requirements(match: MatchResult, filters: dict) -> tuple[bool, str]:
    scope = (match.remote_scope or "unknown").lower()
    if (filters.get("require_remote_worldwide") and scope != "worldwide"
            and not (scope == "unknown" and filters.get("allow_unknown_scope"))):
        return False, f"not remote worldwide ({scope})"
    allowed = {c.upper() for c in filters.get("pay_currencies") or []}
    cur = (match.pay_currency or "unknown").upper()
    if allowed and cur not in allowed and not (cur == "UNKNOWN" and filters.get("allow_unknown_pay")):
        return False, f"pay currency {cur} not in {sorted(allowed)}"
    return True, ""


def worth_scope_search(match: MatchResult, filters: dict, scopes: list[str]) -> bool:
    return (bool(filters.get("require_remote_worldwide"))
            and (match.remote_scope or "unknown").lower() in scopes
            and passes_requirements(replace(match, remote_scope="worldwide"), filters)[0])


def score_job(llm: LLM, profile: dict, job: Job) -> MatchResult:
    prof = {k: profile.get(k) for k in ("summary", "total_experience_years", "skills", "experience", "preferences")}
    user = (f"CANDIDATE:\n{json.dumps(prof, default=str)[:6000]}\n\n"
            f"JOB: {job.title} at {job.company} ({job.location})\n{job.description[:6000]}")
    data = llm.json(MATCH_SYSTEM, user)
    score = max(0, min(100, int(data.get("score", 0))))
    return MatchResult(score=score, reasons=data.get("reasons", []), missing=data.get("missing", []),
                       remote_scope=str(data.get("remote_scope") or "unknown"),
                       pay_currency=str(data.get("pay_currency") or "unknown"))
