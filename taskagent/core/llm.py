import hashlib
import json
import re

import requests


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError(f"No JSON object in LLM output: {text[:200]}")
        return json.loads(m.group(0))


class LLM:
    def __init__(self, cfg: dict, cache=None):
        self.base_url = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.api_key = cfg.get("api_key", "local")
        self.temperature = cfg.get("temperature", 0.1)
        self.timeout = cfg.get("timeout", 180)
        self.json_mode = cfg.get("json_mode", True)
        self.reasoning_effort = cfg.get("reasoning_effort")
        self.cache = cache
        self.session = requests.Session()

    def chat(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        r = self.session.post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def json(self, system: str, user: str) -> dict:
        system += "\nRespond with a single JSON object only."
        key = hashlib.sha256(json.dumps([self.model, system, user]).encode()).hexdigest() if self.cache else None
        cached = self.cache.cache_get(key) if key else None
        if cached is not None:
            try:
                return parse_json(cached)
            except ValueError:
                pass
        text = self.chat(system, user)
        data = parse_json(text)
        if key:
            self.cache.cache_put(key, text)
        return data
