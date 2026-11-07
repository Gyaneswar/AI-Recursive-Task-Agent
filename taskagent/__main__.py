import argparse
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from .config import load_config
from .core.context import Context
from .core.memory import format_hits, ingest
from .core.scheduler import is_due, run_task


def parse_params(items: list[str] | None) -> dict:
    out = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise SystemExit(f"--param expects key=value, got '{item}'")
        out[key.strip()] = yaml.safe_load(value)
    return out


def select(cfg: dict, args) -> list[dict]:
    if args.goal:
        chosen = [{"name": "adhoc", "goal": args.goal}]
    elif args.task:
        known = {t["name"]: t for t in cfg["tasks"]}
        missing = [n for n in args.task if n not in known]
        if missing:
            raise SystemExit(f"unknown task(s) {missing}; known: {list(known)}")
        chosen = [known[n] for n in args.task]
    else:
        chosen = [t for t in cfg["tasks"] if t.get("enabled", True)]
    params, out = parse_params(args.param), []
    for t in chosen:
        if params and t.get("skill"):
            t = {**t, "params": {**(t.get("params") or {}), **params}}
        out.append({**t, "continuous": True} if args.continuous else t)
    return out


def show(ctx: Context, cfg: dict):
    paused = ctx.paused()
    for t in cfg["tasks"]:
        last = ctx.store.last_run(t["name"])
        state = "paused" if t["name"] in paused else "on" if t.get("enabled", True) else "off"
        kind = f"skill:{t['skill']}" if t.get("skill") else "goal"
        every = ("continuous" if t.get("continuous") else f"every {t['every_minutes']}m" if t.get("every_minutes")
                 else "once") + (f" {t['active_hours']}" if t.get("active_hours") else "")
        print(f"{state:<6} {t['name']:<24} {kind:<14} {every:<24} "
              f"last: {f'{last[0]} {last[2]}' if last else 'never'}")


def stop(ctx: Context, names: list[str]):
    if names:
        ctx.set_paused(names, True)
        print(f"paused {names}: a running one stops at its next step; resume with --resume {' '.join(names)}")
    else:
        ctx.request_stop()
        print("stop requested: the running agent finishes its current step and exits")


def memory_cmd(ctx: Context, args):
    mem = ctx.memory
    if mem is None:
        raise SystemExit("memory is disabled; set memory.enabled: true in config.yaml")
    if args.remember:
        print(f"stored memory {mem.add(args.remember, source='user')}")
    for path in args.ingest or []:
        print(f"ingested {ingest(mem, path)} chunks from {path}")
    if args.recall:
        print(format_hits(mem.search(args.recall, 8)))
    print(f"[memory] {mem.count()} entries")


def loop(args):
    ctx = None
    while True:
        start, tick = time.time(), 1.0
        try:
            cfg = load_config(args.config)
            if ctx is None:
                ctx = Context(cfg, Path(args.config).parent, args.dry_run)
                print(f"[loop] started {ctx.started_at}; stop gracefully with: python -m taskagent --stop")
            else:
                ctx.configure(cfg)
            tick = float((cfg.get("schedule") or {}).get("tick_minutes", 1))
            for task in select(cfg, args):
                if ctx.stop_requested():
                    break
                if task["name"] not in ctx.paused() and is_due(task, ctx.store.last_run(task["name"]), datetime.now()):
                    run_task(ctx, task)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            traceback.print_exc()
        deadline = max(start + tick * 60, time.time() + 5)
        while time.time() < deadline:
            if ctx is not None and ctx.stop_requested():
                print("[loop] stop requested - exiting")
                return
            time.sleep(min(5.0, max(0.0, deadline - time.time())))


def main():
    p = argparse.ArgumentParser(prog="taskagent", description="Recursive LLM task agent with pluggable skills")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--task", nargs="+", help="run only these tasks (by name), even if disabled")
    p.add_argument("--goal", help="run an ad-hoc goal through the recursive agent once")
    p.add_argument("--param", action="append", metavar="KEY=VALUE", help="override skill params (YAML value)")
    p.add_argument("--dry-run", action="store_true", help="skills must not submit anything (e.g. jobs fill only)")
    p.add_argument("--loop", action="store_true", help="daemon: reload config every tick and run due tasks")
    p.add_argument("--continuous", action="store_true", help="with --loop: re-run the selected tasks back-to-back")
    p.add_argument("--stop", nargs="*", metavar="TASK",
                   help="graceful stop: no names = stop the running agent; names = pause those tasks")
    p.add_argument("--resume", nargs="+", metavar="TASK", help="resume paused tasks")
    p.add_argument("--list", action="store_true", help="list tasks with their last run")
    p.add_argument("--remember", metavar="TEXT", help="store a fact in long-term memory")
    p.add_argument("--recall", metavar="QUERY", help="search long-term memory")
    p.add_argument("--ingest", nargs="+", metavar="PATH", help="add text files/folders to long-term memory")
    args = p.parse_args()
    if args.loop:
        if args.goal and not args.continuous:
            raise SystemExit("--goal with --loop needs --continuous (or put the goal in the tasks config)")
        return loop(args)
    cfg = load_config(args.config)
    ctx = Context(cfg, Path(args.config).parent, args.dry_run)
    if args.stop is not None:
        return stop(ctx, args.stop)
    if args.resume:
        ctx.set_paused(args.resume, False)
        return print(f"resumed {args.resume}")
    if args.list:
        return show(ctx, cfg)
    if args.remember or args.recall or args.ingest:
        return memory_cmd(ctx, args)
    for task in select(cfg, args):
        run_task(ctx, task)


if __name__ == "__main__":
    main()
