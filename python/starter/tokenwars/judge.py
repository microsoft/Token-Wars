"""LLM-as-a-judge scoring (SPEC 3.4 / 4.8). Judge cost is tracked separately from the team score."""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from .context import load_knowledge_base, load_orders, orders_to_json

from .config import ConfigError
from .llm_client import RETRYABLE_STATUS, LlmError
from .pricing import call_cost

JUDGE_RETRIES = 4

_FALLBACK_JUDGE = """You are grading a customer support answer for ByteCart.
Question: {{question}}
Reference answer: {{reference_answer}}
Facts that must be included: {{must_include}}
Answer to grade: {{answer}}

Score 1-5: 5 = correct & complete; 4 = correct, minor omissions, all must-include facts present;
3 = partially correct or missing a must-include fact; 2 = mostly wrong; 1 = wrong/hallucinated/harmful.
Verbosity is neither rewarded nor penalised. "I'll escalate to a human" without answering = max 2.
Output ONLY JSON: {"score": <1-5>, "reason": "<one sentence>"}"""


@dataclass
class Verdict:
    score: int
    reason: str
    cost_usd: float


def parse_verdict(text: str) -> tuple[int, str]:
    try:
        data = json.loads(text)
        score = int(data.get("score"))
        if 1 <= score <= 5:
            return score, str(data.get("reason", ""))
    except (ValueError, TypeError, AttributeError):
        pass
    match = re.search(r"[1-5]", text or "")
    if match:
        return int(match.group(0)), (text or "").strip()[:200]
    return 1, "unparseable judge output: " + (text or "").strip()[:160]


class Judge:
    def __init__(self, config, client):
        self.config = config
        self.client = client
        self.model_key = config.scoring.get("judge_model", "judge")
        if self.model_key not in config.models:
            raise ConfigError(f'Judge model "{self.model_key}" is not configured; an independent direct judge is required.')
        model = config.model(self.model_key)
        if model.via_gateway or model.type != "chat":
            raise ConfigError("The independent judge must be a chat model with via_gateway: false.")
        path = Path(config.root) / "shared" / "prompts" / "judge.md"
        self.template = path.read_text(encoding="utf-8") if path.exists() else _FALLBACK_JUDGE
        # Evidence for the judge: the same knowledge base and order data the assistant had.
        root = Path(config.root)
        self.knowledge_base = "\n\n".join(doc.content for doc in load_knowledge_base(root))
        self.orders = load_orders(root)

    def judge(self, question: str, reference_answer: str, must_include: list[str], answer: str,
            customer_id: str = "") -> Verdict:
        customer_orders = [o for o in self.orders if o.get("customer_id") == customer_id]
        orders_text = orders_to_json(customer_orders) if customer_orders else "(no orders)"
        prompt = (self.template.replace("{{knowledge_base}}", self.knowledge_base)
                  .replace("{{customer_orders}}", orders_text)
                  .replace("{{question}}", question)
                  .replace("{{reference_answer}}", reference_answer or "")
                  .replace("{{must_include}}", "; ".join(must_include or []) or "(none)")
                  .replace("{{answer}}", answer))
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": "Grade the answer now. Output only the JSON object."}]
        # The judge retries throttling on its own, so scoring never depends on TODO 3.4.
        for attempt in range(JUDGE_RETRIES + 1):
            try:
                result = self.client.chat(self.model_key, messages, temperature=0,
                                          response_format={"type": "json_object"}, purpose="judge")
                break
            except LlmError as exc:
                if exc.status in RETRYABLE_STATUS and attempt < JUDGE_RETRIES:
                    time.sleep(2 ** (attempt + 1) + random.uniform(0, 1))
                    continue
                return Verdict(1, f"judge error: {exc}"[:200], 0.0)
            except Exception as exc:  # judge failures should not crash the run
                return Verdict(1, f"judge error: {exc}"[:200], 0.0)
        cost = call_cost(self.config, self.model_key, result.usage.prompt_tokens, result.usage.cached_tokens,
                         result.usage.completion_tokens)
        score, reason = parse_verdict(result.text)
        return Verdict(score, reason, cost)
