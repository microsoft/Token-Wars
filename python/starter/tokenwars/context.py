"""Context building: knowledge-base retrieval (all / keyword / embedding) and order lookup."""
from __future__ import annotations

import json
import math
import re
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


class ContextBuilder:
    def __init__(self, root: Path, strategy, client=None, embeddings_file: Path | None = None):
        self.strategy = strategy
        self.client = client
        self.docs = load_knowledge_base(root)
        self.sections = split_sections(self.docs)
        self.orders = load_orders(root)
        self.full_kb = "\n\n".join(doc.content for doc in self.docs)
        self.embeddings_file = embeddings_file  # for the embedding-retrieval stretch goal
        # The unique search terms of every section (same order as self.sections) – handy for TODO 1.2.
        self.section_terms = [set(tokenize(section.text)) for section in self.sections]

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

    # TODO 1.2 – Keyword retrieval of the top-k KB sections (Challenge 1 "The Token Diet")
    # retrieval="all" sends the WHOLE knowledge base (thousands of tokens) with every question. Send only the
    # relevant sections instead:
    #   1. query_terms = set(tokenize(question))                      (tokenize() is defined at the top of this file)
    #   2. N = len(self.sections); df(term) = number of sections whose self.section_terms contain the term;
    #      idf(term) = math.log(1 + N / df(term))                    (tip: compute the idf table once and keep it)
    #   3. score(section) = sum of idf over the query terms present in that section
    #   4. take the top_k highest scores (ties keep the original section order – sort by (-score, index))
    #   5. return the section texts (self.sections[i].text) joined with SECTION_SEPARATOR
    # Then set "retrieval": "keyword" in strategy.json.
    def keyword_retrieval(self, question: str, top_k: int) -> str:
        raise NotImplementedError("TODO 1.2 not implemented yet – see tokenwars/context.py")

    # STRETCH GOAL – embedding retrieval (retrieval="embedding"; optional)
    # Embed all section texts once with self.client.embed("embedding", texts) (batch them and append each result to
    # `calls` so the one-off cost is counted), optionally cache the vectors in self.embeddings_file keyed by the
    # embedding deployment, then return the top_k sections by cosine similarity with question_vector().
    def embedding_retrieval(self, question_vector: Callable[[], list[float]], top_k: int, calls: list) -> str:
        raise NotImplementedError("Stretch goal (embedding retrieval) not implemented yet – see tokenwars/context.py")

    # ------------------------------------------------------------------ orders

    def orders_context(self, question: str, customer_id: str, order_specific: bool) -> list[dict[str, Any]]:
        if not self.strategy.order_lookup:
            return self.orders  # baseline: the whole orders database
        return self.lookup_orders(question, customer_id, order_specific)

    # TODO 1.3 – Order lookup: only the relevant orders (Challenge 1 "The Token Diet")
    # order_lookup=false pastes the WHOLE orders database into every prompt – even for "Do you ship to Austria?".
    # Return only what the question needs:
    #   - if the question contains order ids (ORDER_ID_RE, e.g. "BC-10042") -> just the matching order(s)
    #   - elif order_specific -> this customer's 3 most recent orders (sort by "placed_at", newest first)
    #   - else -> [] (no orders at all)
    # Then set "order_lookup": true in strategy.json.
    def lookup_orders(self, question: str, customer_id: str, order_specific: bool) -> list[dict[str, Any]]:
        raise NotImplementedError("TODO 1.3 not implemented yet – see tokenwars/context.py")
