# AI Recursive Task Agent

A local-LLM task agent. You describe tasks in `tasks.yaml`; a daemon runs them on a schedule and picks up edits
without a restart. A task is either:
- **a goal** (plain language): the recursive agent plans with the LLM, splits the goal into subtasks (recursively,
  within depth/step budgets), and solves each with tools: web search, browser page reading, HTTP fetch, workspace
  files, and (if allowed) skills.
- **a skill**: deterministic Python plugin. Built-in: `jobs` (discover jobs, score vs your profile, auto-apply).

## Layout
- `taskagent/core/`: `llm.py` (OpenAI-compatible client + SQLite response cache), `agent.py` (recursive agent),
  `tools.py` (sandboxed tools), `scheduler.py` (due logic, run recording), `context.py` (shared LLM/browser/store),
  `browser.py`, `web.py` (search, HTTP, html->text), `store.py` (`task_runs`, `llm_cache`).
- `taskagent/skills/`: skill registry + `Skill` base; `jobs/` is the job-apply skill (adapters, forms, matcher, DB).
- `config.yaml`: shared settings (llm, browser, search, agent budgets/tools, paths, schedule).
- `tasks.yaml`: what to do. `jobs.yaml`: jobs skill settings. `profile.yaml`: your data (gitignored).

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

### LLM (pick one)
- **Ollama**: `ollama pull qwen2.5:7b-instruct` then keep `llm.base_url: http://localhost:11434/v1`.
- **LM Studio** (better on AMD via Vulkan): load a Qwen2.5 7B Instruct GGUF, start the server,
  set `llm.base_url: http://localhost:1234/v1` and `llm.model` to the loaded model id.

### Browser
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_chrome.ps1
```
First time only: log in to LinkedIn (and any ATS accounts) in that window. Sessions persist in
`%LOCALAPPDATA%\JobBotChrome`. Keep this window open while the agent runs. The browser is attached lazily,
only when a tool or skill needs it.

## Tasks (`tasks.yaml`)
```yaml
tasks:
  - name: jobs                 # skill task
    enabled: true
    skill: jobs
    every_minutes: 60
    params: {config: jobs.yaml, sources: [web, linkedin], discover_only: false}
  - name: weekly-research      # goal task
    goal: Find ... and save a markdown table to reports/x.md
    every_minutes: 10080       # omit to run once
    output: reports/x-summary.md
    agent: {max_steps: 25}     # per-task override of config.yaml `agent`
```
- A task re-runs when `every_minutes` has passed **or its definition changed** (edit a goal -> it runs again).
- Skills: register in `taskagent/skills/__init__.py` (`BUILTIN`) or reference `package.module:Class`
  (subclass `taskagent.skills.base.Skill`, implement `run(params) -> str`).
- The agent may only call skills listed in `config.yaml` `agent.skills` (default none), so a goal cannot
  trigger job applications unless you allow `jobs`. File tools are confined to `paths.workspace`.

## Jobs skill
- `profile.yaml` (gitignored): copy `profile.example.yaml` to `profile.yaml` and fill in your details.
  Empty fields make the bot defer to you instead of guessing.
- `jobs.yaml`: sources, filters, thresholds, limits. `dry_run: true` fills forms but never submits.
- Pipeline: filters -> LLM match score >= `min_match_score` -> fill form -> all required answers resolved and
  confidence >= `min_answer_confidence` -> submit (unless `dry_run`) -> otherwise `data/manual_apply.txt`.
- Outputs: `data/jobbot.db` (`jobs` table with status/score/reason, board registry), `data/screenshots/`.

## Run
```powershell
.\.venv\Scripts\python -m taskagent --list                      # tasks + last run
.\.venv\Scripts\python -m taskagent                             # run enabled tasks once
.\.venv\Scripts\python -m taskagent --task jobs --param discover_only=true --param "sources=[web]"
.\.venv\Scripts\python -m taskagent --goal "Summarise the top 3 posts on news.ycombinator.com into hn.md"
.\.venv\Scripts\python -m taskagent --loop                      # daemon (reloads tasks every tick)
.\.venv\Scripts\python -m taskagent --dry-run                   # skills never submit
powershell -File scripts\run_agent.ps1 --loop                   # ensure LM Studio + Edge, log to data/logs
powershell -File scripts\register_task.ps1                      # start daemon at logon
.\.venv\Scripts\python -m pytest -q
```

## Efficiency
- Identical LLM prompts are answered from `llm_cache` (`llm.cache: true`), e.g. re-scoring the same job.
- One LLM HTTP session, one browser attachment and one store are shared across tasks and ticks.
- Agent budgets (`max_depth`, `max_steps`, `max_node_steps`, `observation_chars`) bound cost; repeated identical
  tool calls are not re-executed; runs and full agent traces are stored in `data/agent.db` (`task_runs`).
