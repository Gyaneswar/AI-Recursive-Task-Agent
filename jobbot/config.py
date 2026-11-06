from pathlib import Path

import yaml


def load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_config(path: str | Path = "config.yaml") -> tuple[dict, dict]:
    cfg = load_yaml(path)
    profile = load_yaml(Path(path).parent / cfg.get("profile", "profile.yaml"))
    for key in ("db", "manual_list", "screenshots"):
        p = Path(cfg["paths"][key])
        (p if key == "screenshots" else p.parent).mkdir(parents=True, exist_ok=True)
    return cfg, profile
