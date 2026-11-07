from collections import Counter
from pathlib import Path

from taskagent.config import load_yaml
from taskagent.core.browser import human_delay

from .adapters import ADAPTERS
from .adapters.boards import find_ats
from .db import DB
from .manual import add_manual
from .matcher import passes_filters, passes_requirements, score_job, worth_scope_search
from .models import ApplyResult
from .websearch import WebSearch


def load_jobs_config(path: str | Path) -> tuple[dict, dict]:
    cfg = load_yaml(path)
    profile = load_yaml(Path(path).parent / cfg.get("profile", "profile.yaml"))
    for key in ("db", "manual_list", "screenshots"):
        p = Path(cfg["paths"][key])
        (p if key == "screenshots" else p.parent).mkdir(parents=True, exist_ok=True)
    return cfg, profile


def skip(job, why: str):
    print(f"[skip] {job.title} @ {job.company} ({job.location}): {why}")


def run(ctx, cfg: dict, profile: dict, sources: list[str], discover_only: bool = False) -> Counter:
    llm, db, stats = ctx.llm, DB(cfg["paths"]["db"]), Counter()
    ws_cfg = cfg.get("web_search") or {}
    needs_browser = (not discover_only or "linkedin" in sources or ws_cfg.get("enabled")
                     or ("web" in sources and (cfg.get("discovery") or {}).get("search_queries")))
    driver = ctx.driver if needs_browser else None
    search = WebSearch(driver, llm, ws_cfg) if ws_cfg.get("enabled") else None
    min_score = cfg["thresholds"]["min_match_score"]
    limits = cfg["limits"]
    daily_cap = limits.get("max_submissions_per_day")

    def record(job, status: str, score: int | None = None, reason: str = ""):
        db.upsert(job, status, score, reason)
        stats[status] += 1

    for name in sources:
        adapter = ADAPTERS[name](cfg, profile, llm, driver)
        submitted = 0
        for job in adapter.discover():
            if submitted >= limits["max_applications_per_run"]:
                print(f"[{name}] reached max_applications_per_run")
                break
            if db.seen(job.id):
                skip(job, "already processed")
                continue
            if db.company_count(job.company) >= limits.get("max_per_company", 2):
                skip(job, f"max_per_company ({limits.get('max_per_company', 2)}) reached")
                continue
            if db.applied_same_role(job):
                skip(job, "same company+title already applied")
                record(job, "duplicate", reason="same company+title already applied")
                continue
            ok, why = passes_filters(job, cfg["filters"])
            if not ok:
                skip(job, why)
                record(job, "filtered", reason=why)
                continue
            try:
                match = score_job(llm, profile, job)
            except Exception as e:
                print(f"[match] {job.title} @ {job.company}: {e}")
                record(job, "error", reason=f"match: {e}"[:300])
                continue
            if job.extra.get("remote_scope"):
                match.remote_scope = job.extra["remote_scope"]
            print(f"[{match.score:3d}] {job.title} @ {job.company} ({job.location}) "
                  f"[{match.remote_scope}, {match.pay_currency}]")
            if match.score < min_score:
                print(f"      -> skipped: low match ({match.score} < {min_score})")
                record(job, "low_match", match.score, "; ".join(match.missing)[:300])
                continue
            ok, why = passes_requirements(match, cfg["filters"])
            if (not ok and search and not job.extra.get("remote_scope")
                    and worth_scope_search(match, cfg["filters"], ws_cfg.get("verify_scopes", ["unknown"]))):
                try:
                    scope, evidence = search.remote_scope(job)
                except Exception as e:
                    scope, evidence = "", f"{type(e).__name__}: {e}"[:200]
                print(f"      [search] {scope or 'skipped'}: {evidence}")
                if scope and scope != "unknown":
                    match.remote_scope = scope
                    ok, why = passes_requirements(match, cfg["filters"])
            if not ok:
                print(f"      -> skipped: {why}")
                record(job, "filtered", match.score, why)
                continue
            if discover_only:
                record(job, "matched", match.score, "; ".join(match.reasons)[:300])
                continue
            if daily_cap and db.count_today() >= daily_cap:
                print(f"[limits] max_submissions_per_day ({daily_cap}) reached")
                return stats
            if name == "linkedin" and db.count_today("linkedin") >= limits["linkedin_daily_cap"]:
                print("LinkedIn daily cap reached")
                break
            try:
                res = adapter.apply(driver, job)
            except Exception as e:
                res = ApplyResult("error", f"{type(e).__name__}: {e}"[:300])
            print(f"      -> {res.status} {res.reason}")
            for ats, slug in find_ats(job.apply_url):
                if db.add_board(ats, slug, f"apply:{name}"):
                    print(f"      [+board] {ats}:{slug}")
            record(job, res.status, match.score, res.reason)
            if res.status in ("needs_manual", "error"):
                add_manual(cfg["paths"]["manual_list"], job, match.score, res.reason)
            if res.status == "submitted":
                submitted += 1
            human_delay(limits["delay_seconds"])
    return stats
