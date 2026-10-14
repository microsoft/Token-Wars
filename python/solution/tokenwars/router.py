"""Model routing: none (default_model), rules (TODO 3.1) or an economy classifier (TODO 3.2)."""
from __future__ import annotations

import re

from .prompts import CLASSIFIER_SYSTEM_PROMPT

# Challenge 2.2: after `compare`, you may change which model serves each tier.
TIER_MODELS = {"SIMPLE": "economy", "STANDARD": "balanced", "COMPLEX": "premium"}

COMPLEXITY_MARKERS = ["but", "however", "although", "both", "and also", "what if", "exception", "combine",
                      "at the same time", "instead"]
COMPLAINT_WORDS = ["angry", "unacceptable", "disappointed", "terrible", "worst", "still haven't", "complaint"]
LONG_QUESTION_CHARS = 280


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(phrase) + r"\b", text) is not None


class Router:
    def __init__(self, strategy, client):
        self.strategy = strategy
        self.client = client

    def route(self, question: str, normalized: str, order_specific: bool, calls: list) -> str:
        if self.strategy.routing == "rules":
            return self.route_rules(question, normalized, order_specific)
        if self.strategy.routing == "classifier":
            return self.route_classifier(question, calls)
        return self.strategy.default_model

    # SOLUTION 3.1 – deterministic rules router.
    def route_rules(self, question: str, normalized: str, order_specific: bool) -> str:
        if order_specific:
            return TIER_MODELS["STANDARD"]
        markers = sum(1 for m in COMPLEXITY_MARKERS if _contains_phrase(normalized, m))
        if len(question.strip()) > LONG_QUESTION_CHARS or markers >= 2:
            return TIER_MODELS["COMPLEX"]
        if any(_contains_phrase(normalized, w) for w in COMPLAINT_WORDS):
            return TIER_MODELS["STANDARD"]
        return TIER_MODELS["SIMPLE"]

    # SOLUTION 3.2 – classifier router: ask economy for SIMPLE / STANDARD / COMPLEX (its tokens are costed).
    def route_classifier(self, question: str, calls: list) -> str:
        result = self.client.chat(
            "economy",
            [{"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT}, {"role": "user", "content": question}],
            temperature=0,
            max_tokens=5,
            purpose="classifier",
        )
        calls.append(result)
        words = result.text.strip().upper().split()
        label = words[0].strip(".,:;!\"'`*") if words else ""
        return TIER_MODELS.get(label, self.strategy.default_model)
