"""Context building: knowledge-base retrieval (all / keyword / embedding) and order lookup."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ORDER_ID_RE = re.compile(r"BC-\d{5}", re.IGNORECASE)
ORDER_MARKERS = ["order", "package", "parcel", "delivery", "delivered", "tracking", "shipped",
                 "refund status", "my purchase"]

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("""
a about after all also am an and any are as at be been before but by can could did do does
for from had has have how i if in into is it its just me my no not of on or our so than that
the their them then there these they this to was we what when where which who why will with
would you your
""".split())

SECTION_SEPARATOR = "\n\n---\n\n"


def tokenize(text: str) -> list[str]:
    """Lowercase [a-z0-9]+ tokens, without stopwords and tokens shorter than 3 chars (SPEC 4.5)."""
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) >= 3 and t not in STOPWORDS]


def normalize_question(question: str) -> str:
    text = question.replace("\u2019", "'").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text.rstrip(" .,!?;:…").strip()


def is_order_specific(question: str) -> bool:
    normalized = normalize_question(question)
    return bool(ORDER_ID_RE.search(normalized)) or any(marker in normalized for marker in ORDER_MARKERS)


@dataclass
class KbDoc:
    file: str
    title: str
    content: str


@dataclass
class KbSection:
    doc_title: str
    title: str
    text: str  # "<Doc Title> > <Section Title>\n<body>"


def load_knowledge_base(root: Path) -> list[KbDoc]:
    kb_dir = root / "shared" / "knowledge-base"
    docs = []
    for path in sorted(kb_dir.glob("*.md"), key=lambda p: p.name):
        content = path.read_text(encoding="utf-8").strip()
        title = path.stem
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        docs.append(KbDoc(path.name, title, content))
    return docs


def split_sections(docs: list[KbDoc]) -> list[KbSection]:
    sections = []
    for doc in docs:
        current_title, body = None, []
        for line in doc.content.splitlines():
            if line.startswith("## "):
                if current_title is not None:
                    sections.append(_section(doc.title, current_title, body))
                current_title, body = line[3:].strip(), []
            elif current_title is not None:
                body.append(line)
        if current_title is not None:
            sections.append(_section(doc.title, current_title, body))
    return sections


def _section(doc_title: str, title: str, body: list[str]) -> KbSection:
    return KbSection(doc_title, title, f"{doc_title} > {title}\n" + "\n".join(body).strip())


def load_orders(root: Path) -> list[dict[str, Any]]:
    path = root / "shared" / "data" / "orders.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def orders_to_json(orders: list[dict[str, Any]]) -> str:
    return json.dumps(orders, separators=(",", ":"), ensure_ascii=False)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class ContextBuilder:
    def __init__(self, root: Path, strategy, client=None, embeddings_file: Path | None = None):
        self.strategy = strategy
        self.client = client
        self.docs = load_knowledge_base(root)
        self.sections = split_sections(self.docs)
        self.orders = load_orders(root)
        self.full_kb = "\n\n".join(doc.content for doc in self.docs)
        self.embeddings_file = embeddings_file
        self._section_vectors: list[list[float]] | None = None
        self._index_lock = threading.Lock()
        self._idf = self._build_idf()

    # ------------------------------------------------------------------ knowledge base

    def kb_context(self, question: str, calls: list, question_vector: Callable[[], list[float]]) -> str:
        mode = self.strategy.retrieval
        if mode == "all":
            return self.full_kb
        if mode == "keyword":
            return self.keyword_retrieval(question, self.strategy.top_k)
        if mode == "embedding":
            return self.embedding_retrieval(question_vector, self.strategy.top_k, calls)
        raise ValueError(f"Unknown retrieval mode {mode}")

    def _build_idf(self) -> dict[str, float]:
        n = len(self.sections)
        df: dict[str, int] = {}
        self._section_terms = [set(tokenize(s.text)) for s in self.sections]
        for terms in self._section_terms:
            for term in terms:
                df[term] = df.get(term, 0) + 1
        return {term: math.log(1 + n / count) for term, count in df.items()}

    # SOLUTION 1.2 – keyword retrieval: score = Σ idf of unique query terms present in the section.
    def keyword_retrieval(self, question: str, top_k: int) -> str:
        query_terms = set(tokenize(question))
        scored = []
        for index, terms in enumerate(self._section_terms):
            score = sum(self._idf[t] for t in query_terms if t in terms)
            scored.append((-score, index))
        scored.sort()  # highest score first, ties keep original order
        return SECTION_SEPARATOR.join(self.sections[i].text for _, i in scored[:top_k])

    # SOLUTION (stretch) – embedding retrieval: embed all sections once, then cosine top-k.
    def embedding_retrieval(self, question_vector: Callable[[], list[float]], top_k: int, calls: list) -> str:
        vectors = self._ensure_section_vectors(calls)
        query = question_vector()
        scored = sorted(((-cosine(query, v), i) for i, v in enumerate(vectors)))
        return SECTION_SEPARATOR.join(self.sections[i].text for _, i in scored[:top_k])

    def _ensure_section_vectors(self, calls: list) -> list[list[float]]:
        with self._index_lock:
            if self._section_vectors is not None:
                return self._section_vectors
            deployment = self.client.config.model("embedding").deployment
            cache_key = ("mock:" if self.client.mock else "") + deployment
            fingerprint = hashlib.sha256("\n\x00".join(s.text for s in self.sections).encode()).hexdigest()
            stored = self._read_embeddings_file()
            entry = stored.get(cache_key)
            if entry and entry.get("fingerprint") == fingerprint and len(entry.get("vectors", [])) == len(self.sections):
                self._section_vectors = entry["vectors"]
                return self._section_vectors
            vectors: list[list[float]] = []
            texts = [s.text for s in self.sections]
            for start in range(0, len(texts), 64):
                result = self.client.embed("embedding", texts[start:start + 64])
                calls.append(result)  # cost is counted once, on the item that triggers the indexing
                vectors.extend(result.vectors)
            self._section_vectors = vectors
            if self.embeddings_file is not None:
                stored[cache_key] = {"fingerprint": fingerprint, "vectors": vectors}
                self.embeddings_file.parent.mkdir(parents=True, exist_ok=True)
                self.embeddings_file.write_text(json.dumps(stored), encoding="utf-8")
            return vectors

    def _read_embeddings_file(self) -> dict[str, Any]:
        if self.embeddings_file is None or not self.embeddings_file.exists():
            return {}
        try:
            return json.loads(self.embeddings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    # ------------------------------------------------------------------ orders

    def orders_context(self, question: str, customer_id: str, order_specific: bool) -> list[dict[str, Any]]:
        if not self.strategy.order_lookup:
            return self.orders  # baseline: the whole orders database
        return self.lookup_orders(question, customer_id, order_specific)

    # SOLUTION 1.3 – order lookup: only the orders the question is about.
    def lookup_orders(self, question: str, customer_id: str, order_specific: bool) -> list[dict[str, Any]]:
        ids = {m.upper() for m in ORDER_ID_RE.findall(question)}
        if ids:
            return [o for o in self.orders if str(o.get("order_id", "")).upper() in ids]
        if order_specific:
            mine = [o for o in self.orders if o.get("customer_id") == customer_id]
            mine.sort(key=lambda o: str(o.get("placed_at", "")), reverse=True)
            return mine[:3]
        return []
