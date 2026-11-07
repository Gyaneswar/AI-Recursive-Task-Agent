import json
from dataclasses import asdict, dataclass, field

SYSTEM = """You are a careful autonomous agent. You work on one GOAL and may delegate parts of it to sub-agents.
Each turn reply with exactly one JSON object, one of:
{"thought": "<short reasoning>", "action": "tool", "tool": "<tool name>", "args": {"<arg>": "<value>"}}
{"thought": "<short reasoning>", "action": "split", "subtasks": ["<self-contained subtask>", "..."]}
{"thought": "<short reasoning>", "action": "finish", "result": "<complete result for the GOAL>"}
Rules:
- "split" only when the GOAL has several distinct parts. Each subtask must make sense on its own. Subtasks run in
  order, later ones see earlier results, and all results come back to you.
- "tool" to get facts or take actions. Never invent facts, URLs or numbers; rely on tool output.
- If a tool fails, try a different approach; never repeat an identical call.
- "finish" as soon as the GOAL is achieved. The result must contain the actual findings or output, not a
  description of what you did."""
FINISH_SYSTEM = """You are out of steps. Using only the HISTORY and earlier results, write the best final result for
the GOAL and state clearly what is missing. Output JSON: {"result": str}"""


@dataclass
class Step:
    action: str
    detail: str
    observation: str


@dataclass
class Node:
    goal: str
    depth: int
    status: str = "running"
    result: str = ""
    steps: list[Step] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class Agent:
    def __init__(self, llm, tools, cfg: dict | None = None, log=print, should_stop=None):
        cfg = cfg or {}
        self.llm, self.tools, self.log = llm, tools, log
        self.should_stop = should_stop or (lambda: False)
        self.max_depth = int(cfg.get("max_depth", 2))
        self.max_steps = int(cfg.get("max_steps", 30))
        self.node_steps = int(cfg.get("max_node_steps", 8))
        self.max_subtasks = int(cfg.get("max_subtasks", 4))
        self.obs_chars = int(cfg.get("observation_chars", 2500))
        self.used = 0

    def run(self, goal: str, notes: list[str] | None = None) -> Node:
        self.used = 0
        return self.solve(str(goal).strip(), 0, [], list(notes or []))

    def _say(self, depth: int, msg: str):
        self.log(f"[agent] {'  ' * depth}{msg}")

    def solve(self, goal: str, depth: int, lineage: list[str], notes: list[str]) -> Node:
        node = Node(goal, depth)
        self._say(depth, f"> {goal[:200]}")
        while len(node.steps) < self.node_steps and self.used < self.max_steps:
            if self.should_stop():
                node.status = "stopped"
                node.result = next((s.observation for s in reversed(node.steps) if s.action != "error"), "")[:1000]
                self._say(depth, "! stop requested")
                return node
            self.used += 1
            try:
                d = self.llm.json(SYSTEM, self.prompt(node, lineage, notes))
                if not isinstance(d, dict):
                    raise ValueError("reply is not a JSON object")
            except Exception as e:
                node.steps.append(Step("error", "", f"invalid reply ({type(e).__name__}: {e}); reply with one JSON object"[:300]))
                continue
            action = str(d.get("action") or "").lower()
            if action == "finish":
                node.status, node.result = "done", str(d.get("result") or "").strip()
                self._say(depth, f"= {node.result[:200]}")
                return node
            if action == "split":
                self.split(node, d.get("subtasks"), lineage, notes)
            elif action == "tool":
                self.act(node, d.get("tool"), d.get("args"))
            else:
                node.steps.append(Step("error", action, 'unknown action; use "tool", "split" or "finish"'))
        return self.wrap_up(node, lineage, notes)

    def split(self, node: Node, subtasks, lineage: list[str], notes: list[str]):
        subs = [str(s).strip() for s in (subtasks if isinstance(subtasks, list) else []) if str(s).strip()]
        if node.depth >= self.max_depth:
            node.steps.append(Step("error", "split", "split not allowed at this depth; solve it with tools"))
            return
        if len(subs) < 2:
            node.steps.append(Step("error", "split", "split needs at least 2 subtasks; otherwise use a tool"))
            return
        subs, done = subs[: self.max_subtasks], []
        for s in subs:
            if self.used >= self.max_steps or self.should_stop():
                done.append(f"- {s} [skipped]: {'stopped' if self.should_stop() else 'step budget exhausted'}")
                continue
            child = self.solve(s, node.depth + 1, lineage + [node.goal], notes + done)
            node.children.append(child)
            done.append(f"- {s} [{child.status}]: {child.result}")
        node.steps.append(Step("split", " | ".join(subs), "\n".join(done)))

    def act(self, node: Node, tool, args):
        detail = f"{tool}({json.dumps(args, ensure_ascii=False, default=str)[:300]})"
        if any(s.action == "tool" and s.detail == detail for s in node.steps):
            obs = "repeated identical call; its result is already in the HISTORY - use it or try something different"
        else:
            self._say(node.depth, f"$ {detail[:160]}")
            obs = self.tools.call(tool, args)
        node.steps.append(Step("tool", detail, obs[: self.obs_chars]))

    def wrap_up(self, node: Node, lineage: list[str], notes: list[str]) -> Node:
        stopped = self.should_stop()
        try:
            if not stopped:
                d = self.llm.json(FINISH_SYSTEM, self.prompt(node, lineage, notes, final=True))
                node.result = str(d.get("result") or "").strip() if isinstance(d, dict) else ""
        except Exception as e:
            self._say(node.depth, f"! wrap-up failed: {type(e).__name__}: {e}"[:200])
        if not node.result:
            node.result = next((s.observation for s in reversed(node.steps) if s.action != "error"), "")[:1000]
        node.status = "stopped" if stopped else "partial"
        self._say(node.depth, f"~ {node.result[:200]}")
        return node

    def prompt(self, node: Node, lineage: list[str], notes: list[str], final: bool = False) -> str:
        parts = [f"GOAL: {node.goal}"]
        if lineage:
            parts.append("PARENT GOALS (context only): " + " > ".join(g[:150] for g in lineage))
        if notes:
            parts.append("NOTES (earlier results and context):\n" + "\n".join(n[:600] for n in notes[-8:]))
        if not final:
            parts.append("TOOLS:\n" + self.tools.describe())
            parts.append("SPLIT: " + (f"allowed (2-{self.max_subtasks} subtasks)" if node.depth < self.max_depth
                                      else "not allowed at this depth"))
        recent = len(node.steps) - 4
        hist = [f"{i}. {s.action} {s.detail}\n   -> {s.observation if i > recent else s.observation[:300]}"
                for i, s in enumerate(node.steps, 1)]
        parts.append("HISTORY:\n" + ("\n".join(hist) if hist else "(none yet)"))
        if not final:
            left = max(1, min(self.node_steps - len(node.steps), self.max_steps - self.used + 1))
            parts.append(f"STEPS LEFT: {left}")
        return "\n\n".join(parts)
