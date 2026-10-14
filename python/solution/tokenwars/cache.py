"""In-memory response caches: exact match (TODO 1.5) and semantic (TODO 1.6)."""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass


@dataclass
class SemanticEntry:
    vector: list[float]
    answer: str


class ResponseCache:
    """Thread-safe: the runner answers several items in parallel."""

    def __init__(self):
        self._lock = threading.Lock()
        self._exact: dict[str, str] = {}
        self._semantic: list[SemanticEntry] = []

    # ------------------------------------------------------------------ exact cache

    # SOLUTION 1.5 – exact-match cache keyed by the normalised question (+ customer for order questions).
    @staticmethod
    def exact_key(normalized_question: str, customer_id: str, order_specific: bool) -> str:
        return f"{normalized_question}|{customer_id}" if order_specific else normalized_question

    def get_exact(self, key: str) -> str | None:
        with self._lock:
            return self._exact.get(key)

    def put_exact(self, key: str, answer: str) -> None:
        with self._lock:
            self._exact[key] = answer

    # ------------------------------------------------------------------ semantic cache

    # SOLUTION 1.6 – semantic cache: cosine similarity against cached (non-order) question embeddings.
    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0

    def get_semantic(self, vector: list[float], threshold: float) -> str | None:
        with self._lock:
            entries = list(self._semantic)
        best_score, best_answer = -1.0, None
        for entry in entries:
            score = self.cosine_similarity(vector, entry.vector)
            if score > best_score:
                best_score, best_answer = score, entry.answer
        return best_answer if best_answer is not None and best_score >= threshold else None

    def put_semantic(self, vector: list[float], answer: str) -> None:
        with self._lock:
            self._semantic.append(SemanticEntry(vector, answer))
