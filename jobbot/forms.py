import json
import os
import re
import time
from dataclasses import dataclass, field

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import Select

from .llm import LLM
from .models import Job

FIELD_JS = r"""
const root = arguments[0] || document;
const pfx = arguments[1] || 'f';
const out = [], groups = {};
const vis = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
const txt = s => (s || '').replace(/\s+/g, ' ').trim();
const labelOf = el => {
  if (el.labels && el.labels.length) return txt(el.labels[0].innerText);
  if (el.getAttribute('aria-label')) return txt(el.getAttribute('aria-label'));
  const lb = el.getAttribute('aria-labelledby');
  if (lb) { const t = document.getElementById(lb.split(' ')[0]); if (t) return txt(t.innerText); }
  const c = el.closest('div,li'); if (c) return txt(c.innerText).slice(0, 200);
  return txt(el.placeholder || el.name);
};
const groupLabel = el => {
  const fs = el.closest('fieldset');
  if (fs && fs.querySelector('legend')) return txt(fs.querySelector('legend').innerText);
  let p = el.parentElement;
  const n = root.querySelectorAll(`[name="${CSS.escape(el.name)}"]`).length;
  while (p && p.querySelectorAll(`[name="${CSS.escape(el.name)}"]`).length < n) p = p.parentElement;
  return p ? txt(p.innerText).slice(0, 300) : '';
};
let i = 0;
root.querySelectorAll('input, select, textarea').forEach(el => {
  const kind = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : (el.type || 'text').toLowerCase();
  if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(kind)) return;
  if (kind !== 'file' && (!vis(el) || el.getAttribute('aria-hidden') === 'true')) return;
  const key = pfx + (i++);
  el.setAttribute('data-jobbot', key);
  const required = el.required || el.getAttribute('aria-required') === 'true';
  if (kind === 'radio' || kind === 'checkbox') {
    const g = el.name || key;
    if (!groups[g]) {
      groups[g] = {key, kind, label: groupLabel(el), required, options: [], option_keys: [], filled: false};
      out.push(groups[g]);
    }
    groups[g].options.push(labelOf(el));
    groups[g].option_keys.push(key);
    groups[g].filled = groups[g].filled || el.checked;
    groups[g].required = groups[g].required || required;
    return;
  }
  const f = {key, kind, label: labelOf(el), hint: `${el.id} ${el.name}`, required, filled: !!el.value,
             combobox: el.getAttribute('role') === 'combobox'};
  if (kind === 'select') {
    f.options = Array.from(el.options).map(o => txt(o.text)).filter(t => t && !/^(select|choose|--)/i.test(t));
    f.filled = el.selectedIndex > 0;
  }
  out.push(f);
});
return out;
"""

ANSWER_SYSTEM = """You fill job application forms on behalf of the candidate.
Rules:
- Use ONLY facts from the candidate profile. Never invent experience, numbers, or authorizations.
- For select/radio pick exactly one option text verbatim. For checkbox return a list of option texts.
- For open-ended questions (e.g. "why this role") write 2-4 honest sentences grounded in the profile and job.
- If the profile does not contain the needed fact, return value null and confidence 0.
Output JSON: {"answers": [{"key": str, "value": str | list | null, "confidence": float 0-1}]}"""

RULES = [
    (r"\bfirst\s*name|given name", "personal.first_name"),
    (r"\blast\s*name|surname|family name", "personal.last_name"),
    (r"\bfull\s*name|^name\b", "_full_name"),
    (r"e-?mail", "personal.email"),
    (r"phone|mobile", "personal.phone"),
    (r"linkedin", "personal.linkedin"),
    (r"github", "personal.github"),
]
TEXT_KINDS = {"text", "email", "tel", "url", "textarea", "number"}
VISIBLE_OPTION_ELS_JS = "return [...document.querySelectorAll('[role=option]')].filter(o => o.offsetWidth || o.offsetHeight)"
VISIBLE_OPTIONS_JS = VISIBLE_OPTION_ELS_JS + ".map(o => o.innerText.trim()).filter(Boolean)"


@dataclass
class FillReport:
    filled: int = 0
    unresolved: list[str] = field(default_factory=list)
    min_confidence: float = 1.0
    answers: list[dict] = field(default_factory=list)


def get_path(profile: dict, path: str):
    if path == "_full_name":
        p = profile.get("personal", {})
        return f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    cur = profile
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur or None


def deterministic_value(field_: dict, profile: dict):
    label = field_["label"].lower()
    if field_["kind"] == "file":
        hint = f"{label} {field_.get('hint', '')}".lower()
        return get_path(profile, "personal.resume_pdf") if re.search(r"resume|\bcv\b", hint) else None
    if field_["kind"] not in TEXT_KINDS or len(label) > 80:
        return None
    for pattern, path in RULES:
        if re.search(pattern, label):
            return get_path(profile, path)
    return None


def match_option(options: list[str], value: str) -> int:
    v = str(value).strip().lower()
    lowered = [o.lower() for o in options]
    for test in (lambda o: o == v, lambda o: o.startswith(v), lambda o: v in o, lambda o: o in v):
        for idx, o in enumerate(lowered):
            if o and test(o):
                return idx
    return -1


def ask_llm(llm: LLM, profile: dict, job: Job, fields: list[dict]) -> dict[str, dict]:
    if not fields:
        return {}
    slim = [{k: f.get(k) for k in ("key", "kind", "label", "options", "required")} for f in fields]
    user = (f"CANDIDATE PROFILE:\n{json.dumps(profile, default=str)[:7000]}\n\n"
            f"JOB: {job.title} at {job.company}\n{job.description[:3000]}\n\n"
            f"FORM FIELDS:\n{json.dumps(slim)}")
    data = llm.json(ANSWER_SYSTEM, user)
    return {a["key"]: a for a in data.get("answers", []) if "key" in a}


def _el(driver, key: str):
    return driver.find_element(By.CSS_SELECTOR, f'[data-jobbot="{key}"]')


def _click(driver, el):
    try:
        el.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", el)


def combobox_options(driver, f: dict) -> list[str]:
    try:
        el = _el(driver, f["key"])
        _click(driver, el)
        time.sleep(0.7)
        opts = driver.execute_script(VISIBLE_OPTIONS_JS)
        el.send_keys(Keys.ESCAPE)
        return opts if len(opts) <= 60 else []
    except WebDriverException:
        return []


def pick_combobox(driver, el, value: str, allow_other: bool = False) -> bool:
    words = value.split()
    queries = list(dict.fromkeys([value, " ".join(words[:2]), words[0] if words else value]))
    if allow_other:
        queries.append("Other")
    for query in queries:
        el.send_keys(Keys.CONTROL, "a")
        el.send_keys(Keys.BACKSPACE)
        el.send_keys(query)
        opts, end = [], time.time() + 4
        while time.time() < end and not (opts := driver.execute_script(VISIBLE_OPTION_ELS_JS)):
            time.sleep(0.3)
        idx = match_option([o.text.strip() for o in opts], "Other" if query == "Other" else value)
        if idx >= 0:
            _click(driver, opts[idx])
            time.sleep(0.5)
            return True
    el.send_keys(Keys.CONTROL, "a")
    el.send_keys(Keys.BACKSPACE)
    el.send_keys(Keys.ESCAPE)
    return False


def set_value(driver, f: dict, value) -> bool:
    kind = f["kind"]
    if kind == "file":
        _el(driver, f["key"]).send_keys(os.path.abspath(str(value)))
    elif kind == "select":
        idx = match_option(f.get("options", []), value)
        if idx < 0:
            return False
        Select(_el(driver, f["key"])).select_by_visible_text(f["options"][idx])
    elif kind in ("radio", "checkbox"):
        values = value if isinstance(value, list) else [value]
        hit = False
        for v in values:
            idx = match_option(f["options"], v)
            if idx >= 0:
                _click(driver, _el(driver, f["option_keys"][idx]))
                hit = True
        return hit
    else:
        el = _el(driver, f["key"])
        if f.get("combobox"):
            return pick_combobox(driver, el, str(value), allow_other=not f.get("options"))
        el.clear()
        el.send_keys(str(value))
    return True


def safe_set(driver, f: dict, value) -> bool:
    try:
        return set_value(driver, f, value)
    except WebDriverException:
        return False


def fill_form(driver, llm: LLM, profile: dict, job: Job, root=None, upload_files: bool = True) -> FillReport:
    report = FillReport()
    prefix = f"jb{int(time.time() * 1000)}_"
    fields = [f for f in driver.execute_script(FIELD_JS, root, prefix) if not f.get("filled")]
    pending = []
    for f in fields:
        if f["kind"] == "file" and not upload_files:
            continue
        v = deterministic_value(f, profile)
        if v:
            report.answers.append({"label": f["label"], "value": v, "confidence": 1.0, "source": "profile"})
            ok = safe_set(driver, f, v)
            report.filled += ok
            if f.get("required") and not ok:
                report.unresolved.append(f["label"][:80])
        elif f["kind"] == "file":
            if f.get("required"):
                report.unresolved.append(f["label"][:80])
        else:
            if f.get("combobox") and not f.get("options"):
                f["options"] = combobox_options(driver, f)
            pending.append(f)
    answers = ask_llm(llm, profile, job, pending)
    for f in pending:
        a = answers.get(f["key"], {})
        value, conf = a.get("value"), float(a.get("confidence") or 0)
        report.answers.append({"label": f["label"], "value": value, "confidence": conf, "source": "llm"})
        ok = value not in (None, "", []) and safe_set(driver, f, value)
        if ok:
            report.filled += 1
        if f.get("required"):
            report.min_confidence = min(report.min_confidence, conf if ok else 0.0)
            if not ok:
                report.unresolved.append(f["label"][:80])
    return report
