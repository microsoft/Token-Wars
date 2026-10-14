"""System/user message construction (baseline, compact and prompt-cache-friendly layouts)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .config import SCENARIO_DATE

# Used only if shared/prompts/system-baseline.md is missing (e.g. an incomplete checkout).
_FALLBACK_BASELINE = """Current date and time: {{now}}
Customer ID: {{customer_id}}

You are ByteCart's friendly Support Copilot. Always give thorough, detailed answers with headings,
an empathy paragraph and a full recap of every relevant policy.

# Knowledge base
{{knowledge_base}}

# Orders database
{{orders}}
"""

# SOLUTION 1.1 – compact system prompt (identical text in Python and .NET).
COMPACT_SYSTEM_PROMPT = (
    "You are ByteCart's customer support assistant. Answer using ONLY the context provided.\n"
    "Be accurate and concise: at most 5 short sentences or bullet points. "
    "Include exact numbers, fees and deadlines from the context.\n"
    "If the context does not contain the answer, say you will connect the customer with a human agent."
)

ESCALATE_INSTRUCTION = ("If you are not confident the context fully answers the question, "
                        "reply with the single word ESCALATE.")

CLASSIFIER_SYSTEM_PROMPT = (
    "Classify the customer support request by the capability needed to answer it well.\n"
    "SIMPLE = a single fact lookup from a policy (e.g. return window, fees, opening hours).\n"
    "STANDARD = needs order data or a short empathetic reply combining 1-2 facts.\n"
    "COMPLEX = needs reasoning across several policies, exceptions or edge cases.\n"
    "Reply with exactly one word: SIMPLE, STANDARD or COMPLEX."
)

_MOVED_TO_USER = "(provided in the user message)"


def load_baseline_template(root: Path) -> str:
    path = root / "shared" / "prompts" / "system-baseline.md"
    return path.read_text(encoding="utf-8") if path.exists() else _FALLBACK_BASELINE


# "Today" for the ByteCart scenario (used in the prompt-cache-friendly layout).
def utc_now_iso() -> str:
    return SCENARIO_DATE + "T" + datetime.now(timezone.utc).strftime("%H:%M:%S") + "Z"


# SOLUTION 2.3 – model-specific prompt adaptation for every non-OpenAI model:
# open-weight (open), self-hosted, the coach's fine-tuned "custom" model and Fireworks models (fw / fw_*).
def model_specific_instructions(model_key: str) -> str:
    if model_key in ("open", "selfhosted", "custom", "fw") or model_key.startswith("fw_"):
        return "Answer in English. Do not invent policies."
    return ""


def extra_instructions(model_key: str, escalation: bool) -> str:
    lines = [model_specific_instructions(model_key)]
    if escalation and model_key != "premium":
        lines.append(ESCALATE_INSTRUCTION)
    return "\n".join(line for line in lines if line)


def context_block(kb_text: str, orders_json: str) -> str:
    parts = []
    if kb_text:
        parts.append(kb_text)
    if orders_json:
        parts.append("Orders:\n" + orders_json)
    return "\n\n".join(parts) if parts else "(no additional context)"


def build_messages(strategy, template: str, model_key: str, question: str, customer_id: str,
                   kb_text: str, orders_json: str) -> list[dict[str, str]]:
    """kb_text = retrieved KB text (full KB when retrieval="all"); orders_json = selected orders ("" = none)."""
    extra = extra_instructions(model_key, strategy.escalation)
    if strategy.prompt_cache_friendly:
        return build_cache_friendly_messages(strategy, template, extra, question, customer_id, kb_text, orders_json)

    if strategy.compact_prompt:
        system = (f"Current date and time: {utc_now_iso()}\nCustomer ID: {customer_id}\n\n"
                  f"{COMPACT_SYSTEM_PROMPT}")
        if extra:
            system += "\n" + extra
        system += "\n\nContext:\n" + context_block(kb_text, orders_json)
    else:
        system = (template.replace("{{now}}", utc_now_iso())
                  .replace("{{customer_id}}", customer_id)
                  .replace("{{knowledge_base}}", kb_text)
                  .replace("{{orders}}", orders_json or "[]"))
        if extra:
            system = system.rstrip() + "\n\n" + extra
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


# SOLUTION 1.7 – prompt-caching-friendly layout: static system message, dynamic data in the user message.
def build_cache_friendly_messages(strategy, template: str, extra: str, question: str, customer_id: str,
                                  kb_text: str, orders_json: str) -> list[dict[str, str]]:
    kb_is_static = strategy.retrieval == "all"
    if strategy.compact_prompt:
        system = COMPACT_SYSTEM_PROMPT + ("\n" + extra if extra else "")
        if kb_is_static:
            system += "\n\nKnowledge base:\n" + kb_text
    else:
        static_lines = [line for line in template.splitlines()
                        if "{{now}}" not in line and "{{customer_id}}" not in line]
        system = ("\n".join(static_lines).strip()
                  .replace("{{knowledge_base}}", kb_text if kb_is_static else _MOVED_TO_USER)
                  .replace("{{orders}}", _MOVED_TO_USER))
        if extra:
            system += "\n\n" + extra
    user = (f"Customer ID: {customer_id}\n"
            f"Today: {SCENARIO_DATE}\n\n"
            f"Context:\n{context_block('' if kb_is_static else kb_text, orders_json)}\n\n"
            f"Question: {question}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
