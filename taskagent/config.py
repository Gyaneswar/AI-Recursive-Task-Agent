from pathlib import Path

import yaml


def load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_tasks(tasks) -> list[dict]:
    if not isinstance(tasks, list):
        raise ValueError("tasks must be a list")
    names = set()
    for t in tasks:
        if not isinstance(t, dict) or not t.get("name"):
            raise ValueError(f"every task needs a name: {t}")
        if bool(t.get("skill")) == bool(t.get("goal")):
            raise ValueError(f"task '{t['name']}' needs exactly one of 'skill' or 'goal'")
        if t["name"] in names:
            raise ValueError(f"duplicate task name '{t['name']}'")
        names.add(t["name"])
    return tasks


def load_config(path: str | Path = "config.yaml") -> dict:
    cfg = load_yaml(path)
    tasks = cfg.get("tasks") or []
    if isinstance(tasks, str):
        data = load_yaml(Path(path).parent / tasks)
        tasks = data if isinstance(data, list) else data.get("tasks") or []
    cfg["tasks"] = check_tasks(tasks)
    return cfg
