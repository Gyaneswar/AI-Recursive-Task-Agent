from taskagent.core.llm import LLM
from taskagent.core.web import BrowserSearch

from .models import Job

GOOGLE = "https://www.google.com/search?hl=en&q={q}"

VERIFY_SYSTEM = """Decide whether a remote job is open to candidates from any country.
You get the job posting and Google search results about the company's remote hiring.
- "worldwide": the posting or results clearly show the company hires remotely from anywhere / globally, and the
  posting does not explicitly limit applicants to specific countries or regions.
- "restricted": the posting or results limit remote hiring to specific countries or regions.
- "unknown": not enough evidence.
A country shown next to a job-board listing is not by itself a restriction. Do not guess.
Output JSON: {"remote_scope": "worldwide" | "restricted" | "unknown", "evidence": str}"""


class WebSearch(BrowserSearch):
    def __init__(self, driver, llm: LLM, cfg: dict):
        super().__init__(driver, cfg, GOOGLE)
        self.llm = llm

    def remote_scope(self, job: Job) -> tuple[str, str]:
        text = self.results(self.cfg["query"].format(company=job.company, title=job.title))
        if not text:
            return "", "no search results"
        user = (f"JOB: {job.title} at {job.company} ({job.location})\n{job.description[:3000]}\n\n"
                f"GOOGLE SEARCH RESULTS:\n{text}")
        data = self.llm.json(VERIFY_SYSTEM, user)
        return str(data.get("remote_scope") or "unknown").lower(), str(data.get("evidence") or "")[:200]
