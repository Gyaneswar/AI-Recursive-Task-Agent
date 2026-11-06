import requests

from ..models import ApplyResult, Job
from .base import Adapter
from .boards import fetch_board


class LeverAdapter(Adapter):
    source = "lever"

    def discover(self):
        for company in self.cfg["sources"].get("lever_companies", []):
            try:
                jobs = fetch_board("lever", company)
            except requests.RequestException as e:
                print(f"[lever] {company}: {e}")
                continue
            if jobs is None:
                print(f"[lever] {company}: board not found (404)")
                continue
            yield from jobs

    def apply(self, driver, job: Job) -> ApplyResult:
        return self.apply_form_page(
            driver, job, job.apply_url,
            form_css="form#application-form, form[action*='apply'], form",
            submit_css="button#btn-submit, button[type=submit]",
        )
