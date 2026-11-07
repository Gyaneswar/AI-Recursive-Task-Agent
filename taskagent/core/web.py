import html
import re
import time
from urllib.parse import parse_qs, quote_plus, urlparse

import requests
from selenium.common.exceptions import WebDriverException

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130 Safari/537.36"}
DDG = "https://html.duckduckgo.com/html/?q={q}"
RESULTS_JS = """
const r = document.querySelector('#search') || document.querySelector('#rso') || document.querySelector('#links')
  || document.body;
return [r.innerText, [...r.querySelectorAll('a[href]')].map(a => [a.innerText.trim(), a.href])];
"""


def html_to_text(s: str) -> str:
    s = html.unescape(html.unescape(s or ""))
    s = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def http_text(url: str, timeout: int = 30, max_chars: int = 20000) -> str:
    if urlparse(str(url)).scheme not in ("http", "https"):
        raise ValueError(f"not an http(s) url: {url}")
    r = requests.get(url, headers=UA, timeout=timeout)
    kind = r.headers.get("content-type", "")
    body = html_to_text(r.text) if "html" in kind else r.text
    return f"HTTP {r.status_code} {kind}\n{body}"[:max_chars]


def result_url(href: str) -> str:
    q = parse_qs(urlparse(href).query)
    for key in ("uddg", "q", "url"):
        if q.get(key) and q[key][0].startswith("http"):
            return q[key][0]
    return href


class BrowserSearch:
    def __init__(self, driver, cfg: dict, default_url: str = DDG):
        self.driver, self.cfg, self.cache = driver, cfg, {}
        self.url = cfg.get("url") or default_url

    def _fetch(self, query: str) -> tuple[str, list]:
        if query in self.cache:
            return self.cache[query]
        main = self.driver.current_window_handle
        text, links = "", []
        try:
            self.driver.switch_to.new_window("tab")
            self.driver.get(self.url.format(q=quote_plus(query)))
            time.sleep(3)
            if "/sorry/" in self.driver.current_url or "captcha" in self.driver.current_url.lower():
                print("[search] CAPTCHA - solve it once in the browser window; skipping search")
            else:
                text, links = self.driver.execute_script(RESULTS_JS) or ("", [])
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
            self.cache[query] = (text, links)
        return text or "", links or []

    def results(self, query: str) -> str:
        return self._fetch(query)[0][: self.cfg.get("max_chars", 4000)]

    def search(self, query: str, n: int = 8) -> str:
        text, links = self._fetch(query)
        engine = urlparse(self.url).netloc.split(".")[-2] if "." in urlparse(self.url).netloc else ""
        seen, out = set(), []
        for label, href in links:
            url = result_url(href)
            host = urlparse(url).netloc
            if not label or not url.startswith("http") or (engine and engine in host) or url in seen:
                continue
            seen.add(url)
            out.append(f"- {label.splitlines()[0][:120]} -> {url}")
            if len(out) >= n:
                break
        if not text:
            return "no results (search failed or CAPTCHA)"
        return "LINKS:\n" + "\n".join(out) + "\n\nPAGE TEXT:\n" + text[: self.cfg.get("max_chars", 4000)]
