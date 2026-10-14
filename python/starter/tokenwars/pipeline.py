"""The answer pipeline for one workload item (SPEC 4.2)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .cache import ResponseCache
from .context import ContextBuilder, is_order_specific, normalize_question, orders_to_json
from .pricing import call_cost
from .prompts import build_messages, load_baseline_template
from .router import TIER_MODELS, Router

# Next model tier when a cheaper model answers ESCALATE (TODO 3.3).
# Any other non-premium key (fw, custom, ...) escalates straight to "premium".
ESCALATION_NEXT = {"economy": "balanced", "balanced": "premium", "open": "premium", "selfhosted": "premium"}


@dataclass
class CallRecord:
    model_key: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class ItemResult:
    id: str
    question: str
    model_path: list[str] = field(default_factory=list)
    cache: str = "none"  # none | exact | semantic
    answer: str = ""
    judge_score: int | None = None
    judge_reason: str | None = None
    success: bool = False
    cost_usd: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    error: str | None = None
    calls: list[CallRecord] = field(default_factory=list, repr=False)

    @property
    def escalations(self) -> int:
        return max(len(self.model_path) - 1, 0)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "question": self.question, "model_path": self.model_path, "cache": self.cache,
            "answer": self.answer, "judge_score": self.judge_score, "judge_reason": self.judge_reason,
            "success": self.success, "cost_usd": round(self.cost_usd, 8), "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens, "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms, "error": self.error,
        }


class Pipeline:
    def __init__(self, config, strategy, client, cache: ResponseCache | None = None,
                 persist_embeddings: bool = True):
        self.config = config
        self.strategy = strategy
        self.client = client
        self.cache = cache or ResponseCache()
        self.template = load_baseline_template(config.root)
        embeddings_file = config.results_dir / ".kb-embeddings.json" if persist_embeddings else None
        self.context = ContextBuilder(config.root, strategy, client, embeddings_file=embeddings_file)
        self.router = Router(strategy, client)
        config.model(strategy.default_model)  # fail early on an unknown default_model
        if strategy.routing != "none":
            for tier_model in TIER_MODELS.values():  # any key from models.json works, typos fail early
                config.model(tier_model)

    # TODO 1.4 – Output control: max tokens + focused style (Challenge 1 "The Token Diet")
    # Output tokens cost 4-6x more than input tokens, and the baseline lets the model write as much as it likes.
    # With reasoning models (GPT-5.6 family) reasoning tokens are billed as output AND count towards this cap:
    # a cap that is too low gives an EMPTY answer (finish_reason "length"), which fails the judge.
    # When strategy.max_output_tokens is set, return (0.2, self.strategy.max_output_tokens): the client then sends
    # the limit as max_tokens / max_completion_tokens (see "max_tokens_param" in models.json) and the lower
    # temperature keeps answers focused. Combine it with the compact prompt (TODO 1.1) so that answers are short
    # by design instead of being cut off mid-sentence. Then set "max_output_tokens" (e.g. 350) in strategy.json.
    def generation_settings(self) -> tuple[float, int | None]:
        """Returns (temperature, max_tokens) for answer calls."""
        if self.strategy.max_output_tokens is not None:
            raise NotImplementedError("TODO 1.4 not implemented yet – see tokenwars/pipeline.py")
        return 0.7, None  # baseline: creative temperature, no output limit

    def answer(self, item: dict[str, Any]) -> ItemResult:
        """Only `id`, `customer_id` and `question` may be used here (ground truth is for the judge only)."""
        question, customer_id = item["question"], item.get("customer_id") or ""
        result = ItemResult(id=item["id"], question=question)
        calls: list = []
        started = time.perf_counter()
        try:
            self._answer(question, customer_id, result, calls)
        except NotImplementedError:
            raise
        except Exception as exc:  # recorded as a failed item
            result.error = f"{type(exc).__name__}: {exc}"
            result.answer = ""
        finally:
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            self._account(result, calls)
        return result

    def _answer(self, question: str, customer_id: str, result: ItemResult, calls: list) -> None:
        # 1-2. normalise + detect order-specific questions
        normalized = normalize_question(question)
        order_specific = is_order_specific(question)

        memo: dict[str, list[float]] = {}

        def question_vector() -> list[float]:
            if "v" not in memo:
                embedded = self.client.embed("embedding", [normalized])
                calls.append(embedded)
                memo["v"] = embedded.vectors[0]
            return memo["v"]

        # 3. exact cache
        exact_key = None
        if self.strategy.exact_cache:
            exact_key = self.cache.exact_key(normalized, customer_id, order_specific)
            cached = self.cache.get_exact(exact_key)
            if cached is not None:
                result.cache, result.answer = "exact", cached
                return

        # 4. semantic cache (never for order-specific questions)
        use_semantic = self.strategy.semantic_cache and not order_specific
        if use_semantic:
            cached = self.cache.get_semantic(question_vector(), self.strategy.semantic_cache_threshold)
            if cached is not None:
                result.cache, result.answer = "semantic", cached
                return

        # 5. routing
        model_key = self.router.route(question, normalized, order_specific, calls)
        result.model_path.append(model_key)

        # 6-7. context + model call
        answer = self._call_model(model_key, question, customer_id, order_specific, calls, question_vector)

        # 8. TODO 3.3 – Escalation on ESCALATE (Challenge 3 "Route & Rule")
        # With escalation=true, non-premium models are told to reply with the single word ESCALATE when unsure
        # (see ESCALATE_INSTRUCTION in prompts.py). Implement the safety net:
        #   while model_key != "premium" and answer.strip().upper() starts with "ESCALATE":
        #       next_key = ESCALATION_NEXT.get(model_key, "premium")   # keys not in the map (fw,
        #                                                               # custom, ...) escalate straight to premium
        #       if next_key is not configured (not in self.config.models): break
        #       model_key = next_key; result.model_path.append(model_key)
        #       answer = self._call_model(model_key, question, customer_id, order_specific, calls, question_vector)
        # Then set "escalation": true in strategy.json (cheap first, expensive only when needed).
        if self.strategy.escalation:
            raise NotImplementedError("TODO 3.3 not implemented yet – see tokenwars/pipeline.py")

        result.answer = answer

        # 9. store in caches (never cache an empty answer)
        if not answer.strip():
            return
        if exact_key is not None:
            self.cache.put_exact(exact_key, answer)
        if use_semantic:
            self.cache.put_semantic(question_vector(), answer)

    def _call_model(self, model_key, question, customer_id, order_specific, calls, question_vector) -> str:
        kb_text = self.context.kb_context(question, calls, question_vector)
        orders = self.context.orders_context(question, customer_id, order_specific)
        orders_json = orders_to_json(orders) if orders else ""
        messages = build_messages(self.strategy, self.template, model_key, question, customer_id, kb_text,
                                  orders_json)
        temperature, max_tokens = self.generation_settings()
        response = self.client.chat(model_key, messages, temperature=temperature, max_tokens=max_tokens)
        calls.append(response)
        return response.text

    def _account(self, result: ItemResult, calls: list) -> None:
        for call in calls:
            usage = call.usage
            cost = call_cost(self.config, call.model_key, usage.prompt_tokens, usage.cached_tokens,
                             usage.completion_tokens)
            result.calls.append(CallRecord(call.model_key, usage.prompt_tokens, usage.cached_tokens,
                                           usage.completion_tokens, cost))
            result.cost_usd += cost
            result.input_tokens += usage.prompt_tokens
            result.cached_input_tokens += usage.cached_tokens
            result.output_tokens += usage.completion_tokens
