from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from taskagent import skills
from taskagent.__main__ import parse_params, select
from taskagent.config import load_config
from taskagent.core.agent import FINISH_SYSTEM, Agent
from taskagent.core.llm import LLM
from taskagent.core.scheduler import fingerprint, is_due, run_task
from taskagent.core.store import Store
from taskagent.core.tools import Tool, Toolbox, make_toolbox
from taskagent.core.web import BrowserSearch, html_to_text, result_url
from taskagent.skills.base import Skill
from taskagent.skills.jobs import JobsSkill, runner

QUIET = lambda msg: None


class ScriptLLM:
    def __init__(self, script):
        self.script = {g: list(r) for g, r in script.items()}
        self.calls = []

    def json(self, system, user):
        goal = user.split("\n", 1)[0].removeprefix("GOAL: ")
        self.calls.append((goal, system, user))
        if system == FINISH_SYSTEM:
            return {"result": f"partial:{goal}"}
        replies = self.script[goal]
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def echo_box(calls=None):
    def echo(text):
        if calls is not None:
            calls.append(text)
        return f"echo:{text}"
    return Toolbox([Tool("echo", "Echo text back.", {"text": "text"}, echo)])


def prompts(llm, goal):
    return [u for g, s, u in llm.calls if g == goal]


def test_agent_recurses_and_passes_results_between_subtasks():
    llm = ScriptLLM({
        "root": [{"action": "split", "subtasks": ["A", "B"]}, {"action": "finish", "result": "root done"}],
        "A": [{"action": "tool", "tool": "echo", "args": {"text": "hi"}}, {"action": "finish", "result": "a-result"}],
        "B": [{"action": "finish", "result": "b-result"}],
    })
    node = Agent(llm, echo_box(), {"max_depth": 1}, QUIET).run("root")
    assert (node.status, node.result) == ("done", "root done")
    assert [(c.goal, c.status, c.result) for c in node.children] == [("A", "done", "a-result"), ("B", "done", "b-result")]
    assert node.children[0].steps[0].observation == "echo:hi"
    b = prompts(llm, "B")[0]
    assert "PARENT GOALS (context only): root" in b and "a-result" in b and "SPLIT: not allowed" in b
    assert "b-result" in prompts(llm, "root")[1] and "SPLIT: allowed" in prompts(llm, "root")[0]
    assert node.to_dict()["children"][1]["result"] == "b-result"


def test_agent_guards_depth_repeats_and_bad_actions_then_wraps_up():
    calls = []
    llm = ScriptLLM({"g": [{"action": "split", "subtasks": ["x", "y"]},
                           {"action": "tool", "tool": "echo", "args": {"text": "1"}},
                           {"action": "tool", "tool": "echo", "args": {"text": "1"}},
                           {"action": "bogus"}]})
    node = Agent(llm, echo_box(calls), {"max_depth": 0, "max_node_steps": 4}, QUIET).run("g")
    assert calls == ["1"]
    assert [s.action for s in node.steps] == ["error", "tool", "tool", "error"]
    assert "not allowed" in node.steps[0].observation and "repeated" in node.steps[2].observation
    assert (node.status, node.result) == ("partial", "partial:g")
    assert node.children == []


def test_agent_split_needs_two_subtasks_and_recovers_from_invalid_reply():
    llm = ScriptLLM({"g": [ValueError("no json"), {"action": "split", "subtasks": ["only one"]},
                           {"action": "finish", "result": "ok"}]})
    node = Agent(llm, echo_box(), {}, QUIET).run("g")
    assert node.status == "done" and node.result == "ok"
    assert "invalid reply" in node.steps[0].observation and "at least 2" in node.steps[1].observation


def test_agent_global_step_budget_skips_remaining_subtasks():
    llm = ScriptLLM({"root": [{"action": "split", "subtasks": ["A", "B", "C"]}],
                     "A": [{"action": "tool", "tool": "echo", "args": {"text": "a"}},
                           {"action": "tool", "tool": "echo", "args": {"text": "b"}}]})
    node = Agent(llm, echo_box(), {"max_steps": 3}, QUIET).run("root")
    assert [c.goal for c in node.children] == ["A"] and node.children[0].status == "partial"
    assert node.steps[0].observation.count("[skipped]") == 2
    assert node.status == "partial"


def test_toolbox_call_errors_and_arg_handling():
    box = Toolbox([Tool("add", "Add.", {"a": "int", "b": "int"}, lambda a, b=0: int(a) + int(b)),
                   Tool("boom", "Fails.", {}, lambda: 1 / 0)])
    assert box.call("add", {"a": 2, "b": 3, "junk": 1}) == "5"
    assert box.call("add", "4") == "4"
    assert box.call("nope", {}).startswith("error: unknown tool 'nope'")
    assert box.call("boom", None).startswith("error: ZeroDivisionError")
    assert box.call("add", {}).startswith("error: TypeError")
    assert "- add(a, b): Add. [a: int; b: int]" in box.describe()


class EchoSkill(Skill):
    name = "echo"
    description = "echo params"

    def run(self, params):
        return f"echo {params}"


def test_workspace_tools_are_sandboxed_and_skills_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setitem(skills.BUILTIN, "echo", f"{__name__}:EchoSkill")
    ctx = SimpleNamespace(workspace=tmp_path / "ws", run_skill=lambda name, params: f"ran {name} {params}")
    box = make_toolbox(ctx, {"tools": ["read_file", "write_file", "list_files"]})
    assert set(box.tools) == {"read_file", "write_file", "list_files"}
    assert box.call("list_files", {}) == "(workspace is empty)"
    assert box.call("write_file", {"path": "r/a.md", "content": "x"}) == "wrote 1 chars to r/a.md"
    assert box.call("write_file", {"path": "r/a.md", "content": "y", "append": "true"}).startswith("appended")
    assert box.call("read_file", {"path": "r/a.md"}) == "xy"
    assert box.call("list_files", {}).replace("\\", "/") == "r/a.md"
    assert "outside the workspace" in box.call("write_file", {"path": "../evil.txt", "content": "x"})
    assert not (tmp_path / "evil.txt").exists()

    box = make_toolbox(ctx, {"tools": [], "skills": ["echo"]})
    assert list(box.tools) == ["run_skill"] and "echo: echo params" in box.describe()
    assert box.call("run_skill", {"name": "echo", "params": '{"k": 1}'}) == "ran echo {'k': 1}"
    assert "not allowed" in box.call("run_skill", {"name": "jobs"})


def test_load_skill_builtin_dotted_and_errors():
    assert skills.load_skill("jobs") is JobsSkill
    assert skills.load_skill(f"{__name__}:EchoSkill") is EchoSkill
    with pytest.raises(ValueError):
        skills.load_skill("missing")
    with pytest.raises(TypeError):
        skills.load_skill("taskagent.core.store:Store")


def test_llm_json_cache_hits_and_skips_unparseable():
    llm = LLM({"base_url": "http://x", "model": "m"}, cache=Store(":memory:"))
    replies = ['{"a": 1}', "not json", '{"b": 2}']
    sent = []
    llm.chat = lambda system, user: sent.append(user) or replies[len(sent) - 1]
    assert llm.json("s", "u1") == {"a": 1} and llm.json("s", "u1") == {"a": 1} and sent == ["u1"]
    with pytest.raises(ValueError):
        llm.json("s", "u2")
    assert llm.json("s", "u2") == {"b": 2} and sent == ["u1", "u2", "u2"]


def test_scheduler_is_due_by_interval_and_definition_change():
    now = datetime(2026, 1, 1, 12, 0)
    task = {"name": "t", "skill": "s", "every_minutes": 60}
    at = lambda minutes: (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    assert is_due(task, None, now)
    assert not is_due(task, (at(30), fingerprint(task), "done"), now)
    assert is_due(task, (at(61), fingerprint(task), "error"), now)
    assert is_due({**task, "params": {"x": 1}}, (at(1), fingerprint(task), "done"), now)
    once = {"name": "o", "goal": "g"}
    assert is_due(once, None, now) and not is_due(once, (at(9999), fingerprint(once), "done"), now)
    assert is_due({**once, "goal": "new goal"}, (at(1), fingerprint(once), "done"), now)


def test_run_task_records_skill_goal_and_errors(tmp_path):
    store = Store(":memory:")
    llm = ScriptLLM({"say hi": [{"action": "finish", "result": "hi!"}]})

    def run_skill(name, params):
        if name == "bad":
            raise RuntimeError("broken")
        return f"{name}:{params}"
    ctx = SimpleNamespace(store=store, llm=llm, cfg={"agent": {"tools": []}}, workspace=tmp_path, run_skill=run_skill)
    assert run_task(ctx, {"name": "s", "skill": "jobs", "params": {"x": 1}}, QUIET) == ("done", "jobs:{'x': 1}")
    assert run_task(ctx, {"name": "b", "skill": "bad"}, QUIET) == ("error", "RuntimeError: broken")
    assert run_task(ctx, {"name": "g", "goal": "say hi", "output": "out/hi.md"}, QUIET) == ("done", "hi!")
    assert (tmp_path / "out" / "hi.md").read_text(encoding="utf-8") == "hi!"
    assert [store.last_run(n)[2] for n in ("s", "b", "g")] == ["done", "error", "done"]
    assert '"goal": "say hi"' in store.conn.execute("SELECT trace FROM task_runs WHERE task='g'").fetchone()[0]


def test_load_config_tasks_file_and_validation(tmp_path):
    (tmp_path / "tasks.yaml").write_text("tasks:\n  - {name: a, skill: jobs}\n  - {name: b, goal: do it}\n")
    (tmp_path / "config.yaml").write_text("llm: {base_url: x, model: m}\ntasks: tasks.yaml\n")
    assert [t["name"] for t in load_config(tmp_path / "config.yaml")["tasks"]] == ["a", "b"]
    for bad in ("[{name: a}]", "[{name: a, skill: s, goal: g}]", "[{skill: s}]", "[{name: a, skill: s}, {name: a, goal: g}]"):
        (tmp_path / "bad.yaml").write_text(f"tasks: {bad}\n")
        with pytest.raises(ValueError):
            load_config(tmp_path / "bad.yaml")


def test_cli_select_and_params():
    cfg = {"tasks": [{"name": "jobs", "skill": "jobs", "params": {"sources": ["web"]}},
                     {"name": "off", "goal": "g", "enabled": False}]}
    args = lambda **kw: SimpleNamespace(**{"goal": None, "task": None, "param": None, **kw})
    assert [t["name"] for t in select(cfg, args())] == ["jobs"]
    assert [t["name"] for t in select(cfg, args(task=["off"]))] == ["off"]
    assert select(cfg, args(goal="hello")) == [{"name": "adhoc", "goal": "hello"}]
    picked = select(cfg, args(param=["discover_only=true", "sources=[linkedin]"]))
    assert picked[0]["params"] == {"sources": ["linkedin"], "discover_only": True}
    assert parse_params(["n=3", "s=a b"]) == {"n": 3, "s": "a b"}
    with pytest.raises(SystemExit):
        parse_params(["oops"])
    with pytest.raises(SystemExit):
        select(cfg, args(task=["nope"]))


def test_web_helpers():
    assert result_url("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=x") == "https://example.com/a"
    assert result_url("https://www.google.com/url?q=https://e.org/b&sa=U") == "https://e.org/b"
    assert result_url("https://site.io/page") == "https://site.io/page"
    assert html_to_text("<script>var x=1;</script><p>Hello</p><style>.a{}</style>") == "Hello"
    s = BrowserSearch(None, {})
    s._fetch = lambda q: ("snippets", [["Example", "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"],
                                      ["", "https://skip.me"], ["DDG", "https://duckduckgo.com/about"],
                                      ["Example again", "https://example.com"]])
    assert s.search("q") == "LINKS:\n- Example -> https://example.com\n\nPAGE TEXT:\nsnippets"
    s._fetch = lambda q: ("", [])
    assert s.search("q").startswith("no results")


def test_jobs_skill_wraps_runner(monkeypatch):
    seen = {}
    monkeypatch.setattr(runner, "load_jobs_config", lambda path: (seen.setdefault("path", path) and {}, {}))

    def fake_run(ctx, cfg, profile, sources, discover_only):
        seen.update(cfg=cfg, sources=sources, discover_only=discover_only)
        return Counter({"submitted": 2, "filtered": 5})
    monkeypatch.setattr(runner, "run", fake_run)
    ctx = SimpleNamespace(dry_run=True, resolve=lambda p: f"base/{p}")
    out = JobsSkill(ctx).run({"sources": "web", "discover_only": 1})
    assert out == "filtered=5, submitted=2"
    assert seen == {"path": "base/jobs.yaml", "cfg": {"dry_run": True}, "sources": ["web"], "discover_only": True}
    with pytest.raises(ValueError):
        JobsSkill(ctx).run({"sources": ["nope"]})
