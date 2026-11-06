import argparse
import time
import traceback
from datetime import datetime

from selenium.common.exceptions import WebDriverException

from .adapters import ADAPTERS
from .adapters.boards import find_ats
from .browser import attach, human_delay
from .config import load_config
from .db import DB
from .llm import LLM
from .manual import add_manual
from .matcher import passes_filters, passes_requirements, score_job, worth_scope_search
from .models import ApplyResult
from .websearch import WebSearch


def skip(job, why: str):
    print(f"[skip] {job.title} @ {job.company} ({job.location}): {why}")


def ensure_driver(cfg, driver):
    if driver is not None:
        try:
            driver.current_window_handle
            return driver
        except WebDriverException:
            print("[browser] lost connection, re-attaching")
    return attach(cfg["browser"]["debugger_address"], cfg["browser"].get("kind", "chrome"))


def run(args, driver=None):
    cfg, profile = load_config(args.config)
    if args.dry_run:
        cfg["dry_run"] = True
    llm, db = LLM(cfg["llm"]), DB(cfg["paths"]["db"])
    ws_cfg = cfg.get("web_search") or {}
    needs_browser = (not args.discover_only or "linkedin" in args.sources or ws_cfg.get("enabled")
                     or ("web" in args.sources and (cfg.get("discovery") or {}).get("search_queries")))
    driver = ensure_driver(cfg, driver) if needs_browser else driver
    search = WebSearch(driver, llm, ws_cfg) if ws_cfg.get("enabled") else None
    min_score = cfg["thresholds"]["min_match_score"]
    limits = cfg["limits"]
    daily_cap = limits.get("max_submissions_per_day")

    for name in args.sources:
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
                db.upsert(job, "duplicate", reason="same company+title already applied")
                continue
            ok, why = passes_filters(job, cfg["filters"])
            if not ok:
                skip(job, why)
                db.upsert(job, "filtered", reason=why)
                continue
            try:
                match = score_job(llm, profile, job)
            except Exception as e:
                print(f"[match] {job.title} @ {job.company}: {e}")
                db.upsert(job, "error", reason=f"match: {e}"[:300])
                continue
            if job.extra.get("remote_scope"):
                match.remote_scope = job.extra["remote_scope"]
            print(f"[{match.score:3d}] {job.title} @ {job.company} ({job.location}) "
                  f"[{match.remote_scope}, {match.pay_currency}]")
            if match.score < min_score:
                print(f"      -> skipped: low match ({match.score} < {min_score})")
                db.upsert(job, "low_match", match.score, "; ".join(match.missing)[:300])
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
                db.upsert(job, "filtered", match.score, why)
                continue
            if args.discover_only:
                db.upsert(job, "matched", match.score, "; ".join(match.reasons)[:300])
                continue
            if daily_cap and db.count_today() >= daily_cap:
                print(f"[limits] max_submissions_per_day ({daily_cap}) reached")
                return cfg, driver
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
            db.upsert(job, res.status, match.score, res.reason)
            if res.status in ("needs_manual", "error"):
                add_manual(cfg["paths"]["manual_list"], job, match.score, res.reason)
            if res.status == "submitted":
                submitted += 1
            human_delay(limits["delay_seconds"])
    return cfg, driver


def loop(args):
    driver = None
    while True:
        start, interval = time.time(), args.interval
        print(f"\n[loop] cycle start {datetime.now():%Y-%m-%d %H:%M:%S}")
        try:
            cfg, driver = run(args, driver)
            interval = interval or (cfg.get("schedule") or {}).get("interval_minutes", 60)
        except KeyboardInterrupt:
            raise
        except Exception:
            traceback.print_exc()
        wait = max(60.0, (interval or 60) * 60 - (time.time() - start))
        print(f"[loop] cycle took {(time.time() - start) / 60:.1f} min; next in {wait / 60:.0f} min")
        time.sleep(wait)


def main():
    p = argparse.ArgumentParser(prog="jobbot")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--sources", nargs="+", default=["web", "linkedin"], choices=list(ADAPTERS))
    p.add_argument("--discover-only", action="store_true", help="score and store matches without applying")
    p.add_argument("--dry-run", action="store_true", help="fill forms but never submit (overrides config)")
    p.add_argument("--loop", action="store_true", help="run forever, one cycle every --interval minutes")
    p.add_argument("--interval", type=float, help="minutes between cycle starts (default schedule.interval_minutes)")
    args = p.parse_args()
    loop(args) if args.loop else run(args)


if __name__ == "__main__":
    main()
