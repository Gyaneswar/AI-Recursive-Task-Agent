import time
from urllib.parse import quote_plus

from selenium.common.exceptions import WebDriverException

from .llm import LLM
from .models import Job

VERIFY_SYSTEM = """Decide whether a remote job is open to candidates from any country.
You get the job posting and Google search results about the company's remote hiring.
- "worldwide": the posting or results clearly show the company hires remotely from anywhere / globally, and the
  posting does not explicitly limit applicants to specific countries or regions.
- "restricted": the posting or results limit remote hiring to specific countries or regions.
- "unknown": not enough evidence.
A country shown next to a job-board listing is not by itself a restriction. Do not guess.
Output JSON: {"remote_scope": "worldwide" | "restricted" | "unknown", "evidence": str}"""
RESULTS_JS = "const r = document.querySelector('#search') || document.querySelector('#rso') || document.body; return r.innerText;"


class WebSearch:
    def __init__(self, driver, llm: LLM, cfg: dict):
        self.driver, self.llm, self.cfg, self.cache = driver, llm, cfg, {}

    def results(self, query: str) -> str:
        if query in self.cache:
            return self.cache[query]
        main = self.driver.current_window_handle
        text = ""
        try:
            self.driver.switch_to.new_window("tab")
            self.driver.get(f"https://www.google.com/search?hl=en&q={quote_plus(query)}")
            time.sleep(3)
            if "/sorry/" in self.driver.current_url:
                print("[search] Google CAPTCHA - solve it once in the browser window; skipping search")
            else:
                text = (self.driver.execute_script(RESULTS_JS) or "")[: self.cfg.get("max_chars", 4000)]
        except WebDriverException as e:
            print(f"[search] {e.msg if hasattr(e, 'msg') else e}")
        finally:
            try:
                if self.driver.current_window_handle != main:
                    self.driver.close()
                self.driver.switch_to.window(main)
            except WebDriverException:
                pass
        if text:
            self.cache[query] = text
        return text

    def remote_scope(self, job: Job) -> tuple[str, str]:
        text = self.results(self.cfg["query"].format(company=job.company, title=job.title))
        if not text:
            return "", "no search results"
        user = (f"JOB: {job.title} at {job.company} ({job.location})\n{job.description[:3000]}\n\n"
                f"GOOGLE SEARCH RESULTS:\n{text}")
        data = self.llm.json(VERIFY_SYSTEM, user)
        return str(data.get("remote_scope") or "unknown").lower(), str(data.get("evidence") or "")[:200]
