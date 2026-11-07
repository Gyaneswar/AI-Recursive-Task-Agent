import argparse
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from .config import load_config
from .core.context import Context
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
        return [{"name": "adhoc", "goal": args.goal}]
    tasks = cfg["tasks"]
    if args.task:
        known = {t["name"]: t for t in tasks}
        missing = [n for n in args.task if n not in known]
        if missing:
            raise SystemExit(f"unknown task(s) {missing}; known: {list(known)}")
        chosen = [known[n] for n in args.task]
    else:
        chosen = [t for t in tasks if t.get("enabled", True)]
    params = parse_params(args.param)
    return [{**t, "params": {**(t.get("params") or {}), **params}} if params and t.get("skill") else t
            for t in chosen]


def show(ctx: Context, cfg: dict):
    for t in cfg["tasks"]:
        last = ctx.store.last_run(t["name"])
        kind = f"skill:{t['skill']}" if t.get("skill") else "goal"
        every = f"every {t['every_minutes']}m" if t.get("every_minutes") else "once"
        print(f"{'on ' if t.get('enabled', True) else 'off'} {t['name']:<24} {kind:<14} {every:<14} "
              f"last: {f'{last[0]} {last[2]}' if last else 'never'}")


def loop(args):
    ctx = None
    while True:
        start, tick = time.time(), 1.0
        try:
            cfg = load_config(args.config)
            if ctx is None:
                ctx = Context(cfg, Path(args.config).parent, args.dry_run)
            else:
                ctx.configure(cfg)
            tick = float((cfg.get("schedule") or {}).get("tick_minutes", 1))
            now = datetime.now()
            for task in select(cfg, args):
                if is_due(task, ctx.store.last_run(task["name"]), now):
                    run_task(ctx, task)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            traceback.print_exc()
        time.sleep(max(5.0, tick * 60 - (time.time() - start)))


def main():
    p = argparse.ArgumentParser(prog="taskagent", description="Recursive LLM task agent with pluggable skills")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--task", nargs="+", help="run only these tasks (by name), even if disabled")
    p.add_argument("--goal", help="run an ad-hoc goal through the recursive agent once")
    p.add_argument("--param", action="append", metavar="KEY=VALUE", help="override skill params (YAML value)")
    p.add_argument("--dry-run", action="store_true", help="skills must not submit anything (e.g. jobs fill only)")
    p.add_argument("--loop", action="store_true", help="daemon: reload config every tick and run due tasks")
    p.add_argument("--list", action="store_true", help="list tasks with their last run")
    args = p.parse_args()
    if args.loop:
        if args.goal:
            raise SystemExit("--goal runs once; put recurring goals in the tasks config")
        return loop(args)
    cfg = load_config(args.config)
    ctx = Context(cfg, Path(args.config).parent, args.dry_run)
    if args.list:
        return show(ctx, cfg)
    for task in select(cfg, args):
        run_task(ctx, task)


if __name__ == "__main__":
    main()
