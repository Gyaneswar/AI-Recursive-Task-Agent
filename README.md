# AIJobApply

Local-LLM job bot: discovers jobs (Greenhouse, Lever, LinkedIn), scores them against your profile,
fills application forms in your logged-in Chrome, auto-submits above a confidence threshold, and
logs everything else to `data/manual_apply.txt`.

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
`%LOCALAPPDATA%\JobBotChrome`. Keep this window open while the bot runs.

## Configure
- `profile.yaml` (gitignored): copy `profile.example.yaml` to `profile.yaml` and fill in your details.
  Empty fields make the bot defer to you instead of guessing.
- `config.yaml`: sources (Greenhouse board tokens, Lever company slugs, LinkedIn search URLs),
  filters, thresholds, limits. `dry_run: true` fills forms but never submits.

## Run
```powershell
.\.venv\Scripts\python -m jobbot --discover-only --sources greenhouse lever
.\.venv\Scripts\python -m jobbot --sources greenhouse lever linkedin
.\.venv\Scripts\python -m pytest -q
```

## Outputs
- `data/jobbot.db`: SQLite table `jobs` with status (`filtered`, `low_match`, `matched`, `dry_run`,
  `needs_manual`, `submitted`, `error`), score, reason.
- `data/manual_apply.txt`: matched jobs needing manual action (unsupported site, low-confidence
  answers, captcha, etc).
- `data/screenshots/`: screenshot per attempt.

## Status flow
filters -> LLM match score >= `min_match_score` -> fill form -> all required answers resolved and
confidence >= `min_answer_confidence` -> submit (unless `dry_run`) -> otherwise manual list.
