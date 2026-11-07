from taskagent.skills.base import Skill

from . import runner
from .adapters import ADAPTERS


class JobsSkill(Skill):
    name = "jobs"
    description = ("discover jobs, score them against the candidate profile and apply. "
                   f"params: sources (list of {', '.join(ADAPTERS)}), discover_only (bool)")

    def run(self, params: dict) -> str:
        cfg, profile = runner.load_jobs_config(self.ctx.resolve(params.get("config", "jobs.yaml")))
        if self.ctx.dry_run or params.get("dry_run"):
            cfg["dry_run"] = True
        sources = params.get("sources") or ["web", "linkedin"]
        sources = [sources] if isinstance(sources, str) else list(sources)
        unknown = [s for s in sources if s not in ADAPTERS]
        if unknown:
            raise ValueError(f"unknown sources {unknown}; choose from {list(ADAPTERS)}")
        stats = runner.run(self.ctx, cfg, profile, sources, bool(params.get("discover_only")))
        return ", ".join(f"{k}={v}" for k, v in sorted(stats.items())) or "no new jobs"
