import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import yaml

from ..skills import load_skill
from .web import http_text

PAGE_JS = "return [document.title, document.body ? document.body.innerText : ''];"


@dataclass
class Tool:
    name: str
    description: str
    args: dict
    fn: Callable[..., str]


class Toolbox:
    def __init__(self, tools: list[Tool]):
        self.tools = {t.name: t for t in tools}

    def describe(self) -> str:
        lines = []
        for t in self.tools.values():
            args = "; ".join(f"{k}: {v}" for k, v in t.args.items())
            lines.append(f"- {t.name}({', '.join(t.args)}): {t.description}" + (f" [{args}]" if args else ""))
        return "\n".join(lines)

    def call(self, name, args) -> str:
        tool = self.tools.get(str(name))
        if tool is None:
            return f"error: unknown tool '{name}'; available: {', '.join(self.tools)}"
        if not isinstance(args, dict):
            args = {next(iter(tool.args)): args} if tool.args and args not in (None, "") else {}
        try:
            return str(tool.fn(**{k: v for k, v in args.items() if k in tool.args}))
        except Exception as e:
            return f"error: {type(e).__name__}: {e}"[:500]


def workspace_path(root: Path, path: str) -> Path:
    base = Path(root).resolve()
    p = (base / str(path)).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"path '{path}' is outside the workspace")
    return p


def _check_url(url: str) -> str:
    if urlparse(str(url)).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) url: {url}")
    return str(url)


def builtin_tools(ctx, skills: list[str] = ()) -> list[Tool]:
    def web_search(query):
        return ctx.search.search(str(query))

    def open_page(url):
        driver = ctx.driver
        driver.get(_check_url(url))
        time.sleep(2)
        title, text = driver.execute_script(PAGE_JS)
        return f"{title}\n{driver.current_url}\n\n{text}"[:20000]

    def http_get(url):
        return http_text(str(url))

    def read_file(path):
        return workspace_path(ctx.workspace, path).read_text(encoding="utf-8")[:20000]

    def write_file(path, content, append=False):
        p = workspace_path(ctx.workspace, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        append = str(append).lower() in ("true", "1", "yes")
        with open(p, "a" if append else "w", encoding="utf-8") as f:
            f.write(str(content))
        return f"{'appended' if append else 'wrote'} {len(str(content))} chars to {path}"

    def list_files():
        root = Path(ctx.workspace)
        files = [str(p.relative_to(root)) for p in sorted(root.rglob("*")) if p.is_file()] if root.exists() else []
        return "\n".join(files[:200]) or "(workspace is empty)"

    def run_skill(name, params=None):
        if name not in skills:
            return f"error: skill '{name}' is not allowed; allowed: {', '.join(skills)}"
        if isinstance(params, str):
            params = yaml.safe_load(params) if params.strip() else {}
        return ctx.run_skill(name, params if isinstance(params, dict) else {})

    tools = [
        Tool("web_search", "Search the web; returns result links and snippets.", {"query": "search terms"}, web_search),
        Tool("open_page", "Open a URL in the logged-in browser (JavaScript sites) and return its visible text.",
             {"url": "http(s) URL"}, open_page),
        Tool("http_get", "Fetch a URL without the browser (fast: APIs, RSS, static pages) and return its text.",
             {"url": "http(s) URL"}, http_get),
        Tool("read_file", "Read a text file from the workspace.", {"path": "relative path"}, read_file),
        Tool("write_file", "Write (or append to) a text file in the workspace.",
             {"path": "relative path", "content": "text", "append": "true to append (optional)"}, write_file),
        Tool("list_files", "List files in the workspace.", {}, list_files),
    ]
    if skills:
        desc = "; ".join(f"{s}: {load_skill(s).description}" for s in skills)
        tools.append(Tool("run_skill", f"Run a built-in skill and return its summary. Skills -> {desc}",
                          {"name": "skill name", "params": "JSON object of skill params (optional)"}, run_skill))
    return tools


def make_toolbox(ctx, cfg: dict) -> Toolbox:
    allowed = cfg.get("tools")
    tools = builtin_tools(ctx, list(cfg.get("skills") or []))
    return Toolbox([t for t in tools if allowed is None or t.name in allowed or t.name == "run_skill"])
