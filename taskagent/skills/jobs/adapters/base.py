import re
import time
from abc import ABC, abstractmethod
from typing import Iterable

from selenium.webdriver.common.by import By

from taskagent.core.browser import screenshot
from taskagent.core.web import html_to_text

from ..forms import _click, fill_form
from ..models import ApplyResult, Job

SUCCESS_RE = re.compile(r"thank you|application (has been )?(submitted|received)|we('ve| have) received", re.I)

CAPTCHA_JS = r"""
return [...document.querySelectorAll("iframe[src*='captcha'], iframe[title*='captcha' i], iframe[title*='challenge' i]")]
  .some(f => { const r = f.getBoundingClientRect();
               return r.width > 50 && r.height > 50 && getComputedStyle(f).visibility !== 'hidden'
                      && !/size=invisible/.test(f.src); });
"""

EXTERNAL_FORMS = {
    "greenhouse.io": ("form#application-form, form#application_form, form",
                      "button[type=submit], input[type=submit], #submit_app"),
    "lever.co": ("form#application-form, form[action*='apply'], form",
                 "button#btn-submit, button[type=submit]"),
    "ashbyhq.com": ("form, .ashby-application-form-container, main",
                    "button.ashby-application-form-submit-button, button[type=submit]"),
}


def external_form_for(url: str):
    return next((v for domain, v in EXTERNAL_FORMS.items() if domain in (url or "")), None)


class Adapter(ABC):
    source = "base"

    def __init__(self, cfg: dict, profile: dict, llm, driver=None):
        self.cfg, self.profile, self.llm, self.driver = cfg, profile, llm, driver

    @abstractmethod
    def discover(self) -> Iterable[Job]:
        ...

    @abstractmethod
    def apply(self, driver, job: Job) -> ApplyResult:
        ...

    def _shot(self, driver, job: Job) -> str:
        return screenshot(driver, self.cfg["paths"]["screenshots"], f"{job.source}_{job.company}_{job.title}")

    def gate(self, driver, job: Job, report, final: bool = True) -> ApplyResult | None:
        th = self.cfg["thresholds"]["min_answer_confidence"]
        if report.unresolved:
            return self.leave_for_review(driver, job, f"unresolved: {'; '.join(report.unresolved)[:300]}")
        if report.min_confidence < th:
            return self.leave_for_review(driver, job, f"low confidence {report.min_confidence:.2f}")
        if final and self.cfg.get("dry_run", True):
            return ApplyResult("dry_run", "filled, not submitted (dry_run)", self._shot(driver, job))
        return None

    def wait_success(self, driver, start_url: str, timeout: int = 20) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            body = driver.find_element(By.TAG_NAME, "body").text
            if SUCCESS_RE.search(body) or ("confirmation" in driver.current_url and driver.current_url != start_url):
                return True
            time.sleep(1)
        return False

    def leave_for_review(self, driver, job: Job, reason: str, url: str = "") -> ApplyResult:
        if url.startswith("http") and driver.current_url != url:
            driver.get(url)
            time.sleep(3)
        shot = self._shot(driver, job)
        name = driver.execute_script("const n = window.name; window.name = ''; return n;")
        driver.switch_to.new_window("tab")
        driver.execute_script("window.name = arguments[0]", name)
        return ApplyResult("needs_manual", f"{reason} - left open in a browser tab for review", shot)

    def apply_form_page(self, driver, job: Job, url: str, form_css: str, submit_css: str) -> ApplyResult:
        driver.get(url)
        time.sleep(3)
        form = next((f[0] for css in form_css.split(",")
                     if (f := driver.find_elements(By.CSS_SELECTOR, css.strip()))), None)
        if form is None:
            captcha = driver.execute_script(CAPTCHA_JS)
            return self.leave_for_review(driver, job, "captcha" if captcha else "application form not found")
        report = fill_form(driver, self.llm, self.profile, job, root=form)
        blocked = self.gate(driver, job, report)
        if blocked:
            return blocked
        buttons = form.find_elements(By.CSS_SELECTOR, submit_css)
        if not buttons:
            return self.leave_for_review(driver, job, "submit button not found")
        start = driver.current_url
        driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'});", buttons[0])
        time.sleep(0.5)
        _click(driver, buttons[0])
        if self.wait_success(driver, start):
            return ApplyResult("submitted", "", self._shot(driver, job))
        captcha = driver.execute_script(CAPTCHA_JS)
        return self.leave_for_review(driver, job, "captcha" if captcha else "submit not confirmed")
