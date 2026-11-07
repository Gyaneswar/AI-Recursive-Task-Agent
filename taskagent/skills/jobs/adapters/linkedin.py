import re
import time
from urllib.parse import quote, urlencode

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By

from taskagent.core.browser import human_delay

from ..forms import FillReport, _click, fill_form
from ..models import ApplyResult, Job
from .base import Adapter, external_form_for

SENT_RE = re.compile(r"application (was )?sent|your application was sent", re.I)
CARD_CSS = '[componentkey^="job-card-component-ref-"]'
REMOTE_FILTER = "f_SA_id_225001:272001"
CARDS_JS = r"""
const seen = new Set(), out = [];
for (const el of document.querySelectorAll(arguments[0])) {
  const id = (el.getAttribute('componentkey') || '').split('-').pop();
  if (!/^\d+$/.test(id) || seen.has(id)) continue;
  seen.add(id);
  const lines = el.innerText.split('\n').map(s => s.trim()).filter(s => s && !/^selected,?$/i.test(s));
  const m = el.innerText.match(/\((Remote|Hybrid|On-site)\)/);
  out.push([id, lines[0] || '', m ? m[1] : '']);
}
return out;
"""
APPLY_CSS = "button[aria-label*='Easy Apply'], button[aria-label*='Apply'], a[aria-label*='Apply']"
DETAILS_JS = r"""
const jid = arguments[0], card = arguments[1];
const about = document.getElementById('JobDetails_AboutTheJob_' + jid);
let root = about || document.querySelector('main') || document.body;
while (about && root.parentElement && root.parentElement !== document.body && !root.parentElement.querySelector(card))
  root = root.parentElement;
const label = b => `${b.getAttribute('aria-label') || ''} ${b.innerText || ''}`;
const lines = root.innerText.split('\n').map(s => s.trim()).filter(Boolean);
const stop = lines.findIndex(s => /^(easy apply|apply|save)$/i.test(s));
return {
  title: document.title,
  lines: lines.slice(0, 30),
  header: stop > 0 ? lines.slice(0, Math.min(stop, 12)) : [],
  description: about ? about.innerText : '',
  easy: [...root.querySelectorAll('button, a')].some(b => /easy apply/i.test(label(b))),
  applied: [...root.querySelectorAll('h2')].some(h => /application status/i.test(h.innerText)),
};
"""
PANE_READY_JS = r"""
const a = document.getElementById('JobDetails_AboutTheJob_' + arguments[0]);
return !!a && a.innerText.trim().length > 30;
"""
EASY_BTN_JS = r"""
const vis = b => !!(b.offsetWidth || b.offsetHeight);
const css = "button[aria-label*='Easy Apply']";
for (let p = document.getElementById('JobDetails_AboutTheJob_' + arguments[0]); p && p !== document.body; p = p.parentElement) {
  const b = [...p.querySelectorAll(css)].find(vis);
  if (b) return b;
}
return [...document.querySelectorAll(css)].find(vis) || null;
"""
GEO_JS = r"""
const done = arguments[arguments.length - 1];
fetch('/jobs-guest/api/typeaheadHits?origin=jserp&typeaheadType=GEO&geoTypes=POPULATED_PLACE,ADMIN_DIVISION_2,MARKET_AREA,COUNTRY_REGION&query='
      + encodeURIComponent(arguments[0]))
  .then(r => r.json()).then(h => done(h.length ? h[0].id : null)).catch(() => done(null));
"""
UNFOLLOW_JS = r"""
for (const cb of arguments[0].querySelectorAll('input[type=checkbox]')) {
  const label = cb.labels && cb.labels.length ? cb.labels[0].innerText.trim() : '';
  if (cb.checked && (/follow-company/i.test(cb.id) || /^follow\b/i.test(label))) cb.click();
}
"""
STEP_BUTTONS = [
    ("submit", ("submit application", "submit")),
    ("review", ("review your application", "review")),
    ("next", ("continue to next step", "next", "continue")),
]


def search_url(keywords: str, geo_id: str | None, hours: int | None, easy_apply: bool, remote: bool = False) -> str:
    q = {"keywords": keywords}
    if geo_id:
        q["geoId"] = geo_id
    if hours:
        q["f_TPR"] = f"r{int(hours * 3600)}"
    if easy_apply:
        q["f_AL"] = "true"
    if remote:
        q["f_SAL"] = REMOTE_FILTER
    return "https://www.linkedin.com/jobs/search-results/?" + urlencode(q, quote_via=quote)


class LinkedInAdapter(Adapter):
    source = "linkedin"
    _cards: dict[str, tuple[str, str]] = {}

    def _geo_id(self, location: str) -> str | None:
        if location.isdigit():
            return location
        try:
            if "linkedin.com" not in self.driver.current_url:
                self.driver.get("https://www.linkedin.com/jobs/")
                time.sleep(3)
            self.driver.set_script_timeout(20)
            return self.driver.execute_async_script(GEO_JS, location)
        except WebDriverException:
            return None

    def search_urls(self) -> list[str]:
        src = self.cfg["sources"]
        s = src.get("linkedin_search") or {}
        urls = list(src.get("linkedin_search_urls") or [])
        kws = [s["keywords"]] if isinstance(s.get("keywords"), str) else s.get("keywords") or []
        locs = s.get("location") if isinstance(s.get("location"), list) else [s.get("location")]
        for loc in locs if kws else []:
            geo = self._geo_id(str(loc)) if loc not in (None, "") else None
            if loc not in (None, "") and not geo:
                print(f"[linkedin] location '{loc}' not found on LinkedIn - skipped")
                continue
            if geo and geo != str(loc):
                print(f"[linkedin] location '{loc}' -> geoId {geo}")
            urls += [search_url(k, geo, s.get("posted_within_hours"), s.get("easy_apply_only", True), s.get("remote_only", False))
                     for k in kws]
        return urls

    def _load_results(self, url: str, page: int) -> list[str]:
        sep = "&" if "?" in url else "?"
        self.driver.get(f"{url}{sep}start={25 * page}")
        time.sleep(4)
        if any(x in self.driver.current_url for x in ("/login", "authwall", "checkpoint", "/signup")):
            print("[linkedin] not logged in - log in once in the JobBotEdge window, then rerun")
            return []
        for _ in range(8):
            self.driver.execute_script(
                f"const c=document.querySelectorAll('{CARD_CSS}'); if(c.length) c[c.length-1].scrollIntoView();")
            time.sleep(0.8)
        self._cards = {jid: (title, mode) for jid, title, mode in self.driver.execute_script(CARDS_JS, CARD_CSS)}
        return list(self._cards)

    def _wait_pane(self, jid: str, timeout: int = 10) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.driver.execute_script(PANE_READY_JS, jid):
                time.sleep(1)
                return True
            time.sleep(0.3)
        return False

    def _open_card(self, jid: str, url: str, page: int) -> bool:
        sel = f'[componentkey="job-card-component-ref-{jid}"]'
        if not self.driver.find_elements(By.CSS_SELECTOR, sel):
            self._load_results(url, page)
        els = self.driver.find_elements(By.CSS_SELECTOR, sel)
        if not els:
            return False
        self.driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'});", els[0])
        time.sleep(0.5)
        _click(self.driver, next((e for e in els if e.is_displayed()), els[0]))
        return self._wait_pane(jid)

    def _job_details(self, jid: str) -> Job:
        url = f"https://www.linkedin.com/jobs/view/{jid}/"
        info = self.driver.execute_script(DETAILS_JS, jid, CARD_CSS)
        parts = re.sub(r"^\(\d+\)\s*", "", info["title"]).rsplit(" | ", 2)
        title, company = (parts[0], parts[1]) if len(parts) == 3 else (parts[0], "")
        meta = next((ln for ln in info["lines"] if " · " in ln), "")
        loc = meta.split("·")[0].strip()
        mode = next((ln for ln in info["lines"][:12] if ln in ("Remote", "Hybrid", "On-site")), "")
        return Job(
            id=f"li:{jid}",
            source=self.source,
            company=company,
            title=title,
            url=url,
            location=f"{loc} ({mode})" if loc and mode else loc,
            description="\n\n".join(filter(None, ["\n".join(info.get("header") or []), info["description"]])),
            extra={"easy_apply": info["easy"], "applied": info["applied"]},
        )

    def discover(self):
        pages = self.cfg["sources"].get("linkedin_pages", 1)
        remote_only = (self.cfg["sources"].get("linkedin_search") or {}).get("remote_only", False)
        done: set[str] = set()
        for url in self.search_urls():
            print(f"[linkedin] search: {url}")
            for page in range(pages):
                ids = [i for i in self._load_results(url, page) if i not in done]
                print(f"[linkedin] page {page + 1}: {len(ids)} new jobs")
                if not ids:
                    break
                for jid in ids:
                    done.add(jid)
                    title, mode = self._cards.get(jid, ("", ""))
                    if remote_only and mode in ("Hybrid", "On-site"):
                        print(f"[skip] {title} (linkedin {jid}): {mode} - remote_only")
                        continue
                    try:
                        if not self._open_card(jid, url, page):
                            print(f"[linkedin] job {jid}: details pane did not load")
                            continue
                        job = self._job_details(jid)
                    except WebDriverException as e:
                        print(f"[linkedin] job {jid}: {e.msg if hasattr(e, 'msg') else e}")
                        continue
                    if job.extra.get("applied"):
                        print(f"[skip] {job.title} @ {job.company} ({job.location}): already applied on LinkedIn")
                        continue
                    yield job
                    human_delay(self.cfg["limits"]["delay_seconds"])

    def _modal(self, driver):
        for css in ("dialog[open]", "[role='dialog']"):
            els = [e for e in driver.find_elements(By.CSS_SELECTOR, css) if e.is_displayed()]
            if els:
                return els[0]
        return None

    def _wait_step(self, driver, timeout: int = 10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                modal = self._modal(driver)
                if modal is not None and self._step_button(modal)[0]:
                    return modal
            except WebDriverException:
                pass
            time.sleep(0.5)
        return self._modal(driver)

    def _step_button(self, modal):
        btns = [(b, (b.get_attribute("aria-label") or "").lower(), b.text.strip().lower())
                for b in modal.find_elements(By.TAG_NAME, "button") if b.is_displayed()]
        for name, keys in STEP_BUTTONS:
            for b, aria, text in btns:
                if text in keys or any(aria.startswith(k) for k in keys):
                    return name, b
        return None, None

    def _discard(self, driver):
        try:
            for b in driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Dismiss']"):
                if b.is_displayed():
                    b.click()
                    break
            time.sleep(1.5)
            for b in driver.find_elements(By.CSS_SELECTOR, "dialog[open] button, [role='dialog'] button"):
                if b.is_displayed() and b.text.strip() == "Discard":
                    b.click()
                    break
        except WebDriverException:
            pass

    def external_apply_url(self, driver) -> str:
        btns = driver.find_elements(By.CSS_SELECTOR, APPLY_CSS)
        if not btns:
            return ""
        handles = set(driver.window_handles)
        main = driver.current_window_handle
        btns[0].click()
        time.sleep(4)
        new = [h for h in driver.window_handles if h not in handles]
        if not new:
            return driver.current_url if "linkedin.com" not in driver.current_url else ""
        driver.switch_to.window(new[0])
        url = driver.current_url
        driver.close()
        driver.switch_to.window(main)
        return url

    def apply(self, driver, job: Job) -> ApplyResult:
        jid = job.id.split(":", 1)[-1]
        if not job.extra.get("easy_apply") or not driver.execute_script(PANE_READY_JS, jid):
            driver.get(job.url)
            time.sleep(3)
        if not job.extra.get("easy_apply"):
            job.apply_url = self.external_apply_url(driver)
            spec = external_form_for(job.apply_url)
            if not spec:
                return ApplyResult("needs_manual", "external apply (unsupported site)")
            return self.apply_form_page(driver, job, job.apply_url, *spec)

        btn = driver.execute_script(EASY_BTN_JS, jid)
        if btn is None:
            return ApplyResult("needs_manual", "easy apply button not found", self._shot(driver, job))
        _click(driver, btn)
        total = FillReport()
        for _ in range(12):
            modal = self._wait_step(driver)
            if modal is None:
                return ApplyResult("needs_manual", "easy apply modal not found", self._shot(driver, job))
            step = fill_form(driver, self.llm, self.profile, job, root=modal, upload_files=False)
            total.unresolved += step.unresolved
            total.min_confidence = min(total.min_confidence, step.min_confidence)
            name, btn = self._step_button(modal)
            if name is None:
                res = ApplyResult("needs_manual", "no next/submit button", self._shot(driver, job))
                self._discard(driver)
                return res
            blocked = self.gate(driver, job, total, final=(name == "submit"))
            if blocked:
                self._discard(driver)
                return blocked
            if name == "submit":
                driver.execute_script(UNFOLLOW_JS, modal)
            driver.execute_script("arguments[0].scrollIntoView({block:'center', behavior:'instant'});", btn)
            _click(driver, btn)
            time.sleep(2)
            if name == "submit":
                time.sleep(3)
                body = driver.find_element(By.TAG_NAME, "body").text
                for b in driver.find_elements(By.CSS_SELECTOR, "button[aria-label='Dismiss']"):
                    b.click()
                    break
                if SENT_RE.search(body):
                    return ApplyResult("submitted")
                return ApplyResult("needs_manual", "submit not confirmed", self._shot(driver, job))
            modal = self._modal(driver)
            if modal and [e for e in modal.find_elements(By.CSS_SELECTOR, "[aria-invalid='true']") if e.is_displayed()]:
                res = ApplyResult("needs_manual", "validation error in easy apply", self._shot(driver, job))
                self._discard(driver)
                return res
        self._discard(driver)
        return ApplyResult("needs_manual", "too many easy apply steps", self._shot(driver, job))
