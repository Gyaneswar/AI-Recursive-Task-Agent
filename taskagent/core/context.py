import json
from pathlib import Path

from ..skills import load_skill
from .browser import ensure_driver
from .llm import LLM
from .memory import make_memory
from .store import Store, now
from .web import BrowserSearch


class Context:
    def __init__(self, cfg: dict, base_dir: str | Path = ".", dry_run: bool = False):
        self.base_dir, self.dry_run = Path(base_dir), dry_run
        paths = cfg.get("paths") or {}
        self.store = Store(paths.get("db", "data/agent.db"))
        self.workspace = Path(paths.get("workspace", "data/workspace"))
        self.started_at, self.current_task = now(), None
        self._driver = self._search = self._memory = None
        self._skills = {}
        self.configure(cfg)

    def configure(self, cfg: dict):
        self.cfg = cfg
        llm_cfg = cfg["llm"]
        self.llm = LLM(llm_cfg, cache=self.store if llm_cfg.get("cache", True) else None)

    @property
    def driver(self):
        self._driver = ensure_driver(self.cfg.get("browser") or {}, self._driver)
        return self._driver

    @property
    def search(self) -> BrowserSearch:
        driver = self.driver
        if self._search is None or self._search.driver is not driver:
            self._search = BrowserSearch(driver, self.cfg.get("search") or {})
        return self._search

    @property
    def memory(self):
        if self._memory is None:
            self._memory = make_memory(self.cfg.get("memory") or {}, self.cfg["llm"])
        return self._memory

    def request_stop(self):
        self.store.set_meta("stop_at", now())

    def stop_requested(self) -> bool:
        at = self.store.get_meta("stop_at")
        return bool(at) and at >= self.started_at

    def paused(self) -> set[str]:
        return set(json.loads(self.store.get_meta("paused", "[]")))

    def set_paused(self, names: list[str], paused: bool = True):
        names = self.paused() | set(names) if paused else self.paused() - set(names)
        self.store.set_meta("paused", json.dumps(sorted(names)))

    def should_stop(self) -> bool:
        return self.stop_requested() or (self.current_task is not None and self.current_task in self.paused())

    def resolve(self, path: str | Path) -> Path:
        p = Path(path)
        return p if p.is_absolute() else self.base_dir / p

    def skill(self, name: str):
        if name not in self._skills:
            self._skills[name] = load_skill(name)(self)
        return self._skills[name]

    def run_skill(self, name: str, params: dict | None = None) -> str:
        return self.skill(name).run(dict(params or {}))
