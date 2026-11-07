import requests

from ..models import ApplyResult, Job
from .base import Adapter
from .boards import fetch_board


class GreenhouseAdapter(Adapter):
    source = "greenhouse"

    def discover(self):
        for token in self.cfg["sources"].get("greenhouse_boards", []):
            try:
                jobs = fetch_board("greenhouse", token)
            except requests.RequestException as e:
                print(f"[greenhouse] {token}: {e}")
                continue
            if jobs is None:
                print(f"[greenhouse] {token}: board not found (404)")
                continue
            yield from jobs

    def apply(self, driver, job: Job) -> ApplyResult:
        return self.apply_form_page(
            driver, job, job.apply_url,
            form_css="form#application-form, form#application_form, form",
            submit_css="button[type=submit], input[type=submit], #submit_app",
        )
