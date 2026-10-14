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

    # TODO 3.1 – Rules router (Challenge 3 "Route & Rule")
    # Most questions do not need the premium model. Route deterministically, in this order:
    #   - order_specific                                               -> TIER_MODELS["STANDARD"]  (balanced)
    #   - len(question) > LONG_QUESTION_CHARS or >= 2 COMPLEXITY_MARKERS -> TIER_MODELS["COMPLEX"]   (premium)
    #   - any COMPLAINT_WORDS                                          -> TIER_MODELS["STANDARD"]  (balanced)
    #   - otherwise                                                    -> TIER_MODELS["SIMPLE"]    (economy)
    # Match markers/words on `normalized` with _contains_phrase() so "but" does not match "button".
    # Then set "routing": "rules" in strategy.json.
    def route_rules(self, question: str, normalized: str, order_specific: bool) -> str:
        raise NotImplementedError("TODO 3.1 not implemented yet – see tokenwars/router.py")

    # TODO 3.2 – Classifier router (Challenge 3 "Route & Rule")
    # Let the cheapest model decide: call self.client.chat("economy", messages, temperature=0, max_tokens=5,
    # purpose="classifier") with system = CLASSIFIER_SYSTEM_PROMPT and user = question.
    # Append the ChatResult to `calls` (the classifier tokens are part of your bill!), take the first word of the
    # reply (uppercase, without punctuation) and map it with TIER_MODELS; anything else -> self.strategy.default_model.
    # Then set "routing": "classifier" in strategy.json.
    def route_classifier(self, question: str, calls: list) -> str:
        raise NotImplementedError("TODO 3.2 not implemented yet – see tokenwars/router.py")
