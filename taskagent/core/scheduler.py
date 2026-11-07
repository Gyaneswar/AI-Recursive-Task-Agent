import hashlib
import json
import traceback
from datetime import datetime, time, timedelta

from .agent import Agent
from .tools import make_toolbox, workspace_path


def fingerprint(task: dict) -> str:
    return hashlib.sha1(json.dumps(task, sort_keys=True, default=str).encode()).hexdigest()[:12]


def in_window(task: dict, now: datetime) -> bool:
    hours = task.get("active_hours")
    if not hours:
        return True
    start, _, end = str(hours).partition("-")
    s, e, t = time.fromisoformat(start.strip()), time.fromisoformat(end.strip()), now.time()
    return s <= t < e if s <= e else (t >= s or t < e)


def is_due(task: dict, last: tuple[str, str, str] | None, now: datetime) -> bool:
    if not in_window(task, now):
        return False
    if last is None or last[1] != fingerprint(task) or task.get("continuous"):
        return True
    every = task.get("every_minutes")
    return bool(every) and datetime.fromisoformat(last[0]) + timedelta(minutes=float(every)) <= now


def goal_notes(ctx, task: dict, fp: str, memory, mem_cfg: dict) -> list[str]:
    notes = []
    prev = ctx.store.last_result(task["name"], fp)
    if prev:
        notes.append(f"result of the previous run of this task (continue from it, do not redo it): {prev}")
    k = int(mem_cfg.get("auto_recall", 3)) if memory is not None else 0
    if k:
        floor = float(mem_cfg.get("auto_recall_min_score", 0.3))
        notes += [f"memory [{h['score']:.2f}]: {h['text'][:400]}" for h in memory.search(task["goal"], k)
                  if h["score"] >= floor and not (prev and prev[:200] in h["text"])]
    return notes


def run_task(ctx, task: dict, log=print) -> tuple[str, str]:
    name, fp = task["name"], fingerprint(task)
    log(f"\n[task] {name}: start {datetime.now():%Y-%m-%d %H:%M:%S}")
    ctx.current_task = name
    run_id = ctx.store.start_run(name, fp)
    status, summary, trace = "interrupted", "", ""
    try:
        if task.get("skill"):
            summary = ctx.run_skill(task["skill"], task.get("params") or {})
            status = "stopped" if ctx.should_stop() else "done"
        else:
            cfg = {**(ctx.cfg.get("agent") or {}), **(task.get("agent") or {})}
            mem_cfg, memory = ctx.cfg.get("memory") or {}, ctx.memory
            notes = goal_notes(ctx, task, fp, memory, mem_cfg)
            agent = Agent(ctx.llm, make_toolbox(ctx, cfg, memory), cfg, log, ctx.should_stop)
            node = agent.run(task["goal"], notes)
            status, summary = node.status, node.result
            trace = json.dumps(node.to_dict(), ensure_ascii=False)
            if task.get("output"):
                out = workspace_path(ctx.workspace, task["output"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(summary, encoding="utf-8")
                log(f"[task] {name}: result written to {out}")
            if memory is not None and summary and mem_cfg.get("auto_store", True):
                memory.add(f"Task '{name}' goal: {task['goal']}\nResult ({status}): {summary}", source="task", task=name)
    except Exception as e:
        status, summary = "error", f"{type(e).__name__}: {e}"
        traceback.print_exc()
    finally:
        ctx.store.finish_run(run_id, status, summary[:4000], trace)
        ctx.current_task = None
    log(f"[task] {name} -> {status}: {summary[:300]}")
    return status, summary
