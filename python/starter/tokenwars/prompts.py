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

# TODO 1.1 – Compact system prompt (Challenge 1 "The Token Diet")
# The baseline prompt (shared/prompts/system-baseline.md) is hundreds of tokens of verbose instructions and asks
# for long answers with headings, empathy paragraphs and full policy recaps – you pay for it on EVERY call.
# Replace None with a short, strict system prompt (use the exact text from the workshop website) that says:
#   - who the assistant is and to answer using ONLY the context provided,
#   - be accurate and concise: at most 5 short sentences or bullet points, with exact numbers/fees/deadlines,
#   - if the context does not contain the answer, offer to connect the customer with a human agent.
# Then set "compact_prompt": true in strategy.json. (The rest of the compact layout is already wired up below.)
COMPACT_SYSTEM_PROMPT: str | None = None

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


# TODO 2.3 – Model-specific prompt adaptation (Challenge 2 "Bring Your Own Model")
# Open-weight and small self-hosted models tend to drift (answer in another language, make up policies).
# Return the extra instruction "Answer in English. Do not invent policies." for every non-OpenAI model key:
# "open", "selfhosted", "custom" (coach demo), "fw", and any key that starts with "fw_" (Fireworks)
# – and "" for every other model. It is appended to the system instructions automatically.
def model_specific_instructions(model_key: str) -> str:
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
        if COMPACT_SYSTEM_PROMPT is None:
            raise NotImplementedError("TODO 1.1 not implemented yet – see tokenwars/prompts.py")
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


# TODO 1.7 – Prompt-caching-friendly message layout (Challenge 1 "The Token Diet")
# Azure OpenAI automatically caches the longest identical PREFIX of a prompt (from 1,024 tokens) and bills cached
# input tokens much cheaper. The baseline puts "Current date and time" + "Customer ID" at the very TOP of the system
# message, so the prefix is different on every call and nothing is ever cached.
# Return [system, user] messages where:
#   system = ONLY static content:
#            - compact_prompt=true : COMPACT_SYSTEM_PROMPT (+ "\n" + extra if extra), plus
#                                    "\n\nKnowledge base:\n" + kb_text when strategy.retrieval == "all"
#            - compact_prompt=false: the baseline template WITHOUT the lines containing {{now}} / {{customer_id}};
#                                    fill {{knowledge_base}} with kb_text when retrieval == "all" (else _MOVED_TO_USER)
#                                    and {{orders}} with _MOVED_TO_USER; append extra if any
#   user   = f"Customer ID: {customer_id}\nToday: {SCENARIO_DATE}\n\nContext:\n{...}\n\nQuestion: {question}"
#            where the context is context_block(kb_text or "" if the KB is already in the system message, orders_json)
# Then set "prompt_cache_friendly": true and watch "cached" input tokens appear in the scorecard (real runs only).
def build_cache_friendly_messages(strategy, template: str, extra: str, question: str, customer_id: str,
                                  kb_text: str, orders_json: str) -> list[dict[str, str]]:
    raise NotImplementedError("TODO 1.7 not implemented yet – see tokenwars/prompts.py")
