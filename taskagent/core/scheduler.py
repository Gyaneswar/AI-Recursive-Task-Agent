import hashlib
import json
import traceback
from datetime import datetime, timedelta

from .agent import Agent
from .tools import make_toolbox, workspace_path


def fingerprint(task: dict) -> str:
    return hashlib.sha1(json.dumps(task, sort_keys=True, default=str).encode()).hexdigest()[:12]


def is_due(task: dict, last: tuple[str, str, str] | None, now: datetime) -> bool:
    if last is None or last[1] != fingerprint(task):
        return True
    every = task.get("every_minutes")
    return bool(every) and datetime.fromisoformat(last[0]) + timedelta(minutes=float(every)) <= now


def run_task(ctx, task: dict, log=print) -> tuple[str, str]:
    name = task["name"]
    log(f"\n[task] {name}: start {datetime.now():%Y-%m-%d %H:%M:%S}")
    run_id = ctx.store.start_run(name, fingerprint(task))
    status, summary, trace = "interrupted", "", ""
    try:
        if task.get("skill"):
            summary = ctx.run_skill(task["skill"], task.get("params") or {})
            status = "done"
        else:
            cfg = {**(ctx.cfg.get("agent") or {}), **(task.get("agent") or {})}
            node = Agent(ctx.llm, make_toolbox(ctx, cfg), cfg, log).run(task["goal"])
            status, summary = node.status, node.result
            trace = json.dumps(node.to_dict(), ensure_ascii=False)
            if task.get("output"):
                out = workspace_path(ctx.workspace, task["output"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(summary, encoding="utf-8")
                log(f"[task] {name}: result written to {out}")
    except Exception as e:
        status, summary = "error", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        ctx.store.finish_run(run_id, status, summary[:4000], trace)
    log(f"[task] {name} -> {status}: {summary[:300]}")
    return status, summary
