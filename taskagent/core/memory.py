import hashlib
import re
from datetime import datetime
from pathlib import Path

import requests

from .web import html_to_text

TEXT_EXT = {".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".csv", ".html", ".htm", ".py", ".java", ".log"}


def openai_embedder(base_url: str, model: str, api_key: str = "local", timeout: int = 120):
    session = requests.Session()

    def embed(texts: list[str]) -> list[list[float]]:
        r = session.post(f"{base_url.rstrip('/')}/embeddings", json={"model": model, "input": texts},
                         headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d.get("index", 0))]
    return embed


def chunks(text: str, size: int = 1200) -> list[str]:
    out, cur = [], ""
    for para in (p.strip() for p in re.split(r"\n\s*\n", text or "")):
        for piece in (para[i:i + size] for i in range(0, len(para), size)):
            if cur and len(cur) + len(piece) + 2 > size:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        out.append(cur)
    return out


def _meta(meta: dict) -> dict:
    md = {k: v if isinstance(v, (str, int, float, bool)) else str(v) for k, v in meta.items() if v not in (None, "")}
    md["created_at"] = datetime.now().isoformat(timespec="seconds")
    return md


class Memory:
    def __init__(self, path: str | Path = "data/memory", collection: str = "memory", embed=None, client=None):
        if client is None:
            try:
                import chromadb
                from chromadb.config import Settings
            except ImportError as e:
                raise RuntimeError("memory needs chromadb: pip install chromadb (or set memory.enabled: false)") from e
            client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
        self.col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
        self.embed = embed

    def count(self) -> int:
        return self.col.count()

    def add_many(self, texts: list[str], metas: list[dict] | None = None, batch: int = 64) -> list[str]:
        items = list({str(t).strip(): m for t, m in zip(texts, metas or [{}] * len(texts)) if str(t).strip()}.items())
        ids = []
        for i in range(0, len(items), batch):
            docs = [t for t, _ in items[i:i + batch]]
            kw = {"ids": [hashlib.sha1(t.encode()).hexdigest()[:16] for t in docs], "documents": docs,
                  "metadatas": [_meta(m) for _, m in items[i:i + batch]]}
            if self.embed:
                kw["embeddings"] = self.embed(docs)
            self.col.upsert(**kw)
            ids += kw["ids"]
        return ids

    def add(self, text: str, **meta) -> str:
        ids = self.add_many([text], [meta])
        if not ids:
            raise ValueError("nothing to remember")
        return ids[0]

    def search(self, query: str, k: int = 5) -> list[dict]:
        n = self.col.count()
        if not n or not str(query).strip():
            return []
        kw = {"n_results": max(1, min(int(k), n)), "include": ["documents", "metadatas", "distances"]}
        if self.embed:
            kw["query_embeddings"] = self.embed([str(query)])
        else:
            kw["query_texts"] = [str(query)]
        r = self.col.query(**kw)
        return [{"id": i, "text": d, "meta": m or {}, "score": round(1 - float(dist), 3)}
                for i, d, m, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0], r["distances"][0])]


def format_hits(hits: list[dict], chars: int = 500) -> str:
    lines = []
    for h in hits:
        m = h["meta"]
        tags = ", ".join(f"{k}={m[k]}" for k in ("task", "source", "tags", "file") if m.get(k))
        lines.append(f"- [{h['score']:.2f}{', ' + tags if tags else ''}] {h['text'][:chars]}")
    return "\n".join(lines) or "no memories found"


def ingest(memory: Memory, path: str | Path, size: int = 1200) -> int:
    path = Path(path)
    files = [p for p in sorted(path.rglob("*")) if p.is_file() and p.suffix.lower() in TEXT_EXT] if path.is_dir() else [path]
    total = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if f.suffix.lower() in (".html", ".htm"):
            text = html_to_text(text)
        parts = chunks(text, size)
        total += len(memory.add_many(parts, [{"source": "file", "file": str(f), "chunk": i} for i in range(len(parts))]))
    return total


def make_memory(cfg: dict, llm_cfg: dict) -> Memory | None:
    if not cfg.get("enabled"):
        return None
    model = cfg.get("embedding_model") or ""
    embed = (openai_embedder(cfg.get("embedding_url") or llm_cfg["base_url"], model, llm_cfg.get("api_key", "local"))
             if model else None)
    slug = re.sub(r"[^a-z0-9]+", "-", (model or "default").lower())
    name = f"{cfg.get('collection', 'memory')}-{slug}"[:60].strip("-")
    return Memory(cfg.get("path", "data/memory"), name, embed)
